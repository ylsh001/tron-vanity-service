import os
import subprocess
import tempfile
import time
import runpod
from Crypto.Hash import keccak
from ecdsa import SigningKey, SECP256k1
import base58

BINARY_PATH = "/app/profanity.x64"
BASE58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
MAX_PATTERN_LEN = 8
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 3600
DEFAULT_MATCHES_WANTED = 1
MAX_MATCHES_WANTED = 20
FILLER_CHAR = "A"


def validate_pattern(value, field_name):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if len(value) > MAX_PATTERN_LEN:
        raise ValueError(f"{field_name} length must be <= {MAX_PATTERN_LEN}")
    for ch in value:
        if ch not in BASE58_ALPHABET:
            raise ValueError(f"{field_name} contains invalid base58 character: {ch}")
    return value


def build_matching_pattern(prefix, suffix):
    if prefix and not prefix.startswith("T"):
        prefix = "T" + prefix
    prefix_segment = (prefix + FILLER_CHAR * 10)[:10]
    suffix_segment = (FILLER_CHAR * 10 + suffix)[-10:]
    return prefix_segment + suffix_segment


def derive_tron_address(private_key_hex):
    priv_bytes = bytes.fromhex(private_key_hex)
    signing_key = SigningKey.from_string(priv_bytes, curve=SECP256k1)
    public_key_bytes = signing_key.get_verifying_key().to_string()
    k = keccak.new(digest_bits=256)
    k.update(public_key_bytes)
    address_bytes = b"\x41" + k.digest()[-20:]
    checksum = keccak_sha256_checksum(address_bytes)
    return base58.b58encode(address_bytes + checksum).decode()


def keccak_sha256_checksum(payload):
    from Crypto.Hash import SHA256
    first = SHA256.new(payload).digest()
    second = SHA256.new(first).digest()
    return second[:4]


def parse_result_file(result_path):
    matches = []
    if not os.path.exists(result_path):
        return matches
    with open(result_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) != 2:
                continue
            private_key, address = parts[0].strip(), parts[1].strip()
            matches.append({"private_key": private_key, "address": address})
    return matches


def run_diagnostics():
    info = {}
    for name, cmd in (
        ("nvidia_smi", ["nvidia-smi"]),
        ("clinfo", ["clinfo"]),
    ):
        try:
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30
            )
            info[name] = result.stdout
        except Exception as e:
            info[name] = f"error running {' '.join(cmd)}: {e}"
    return info


def handler(event):
    job_input = event.get("input", {}) or {}

    if job_input.get("diagnose"):
        return {"status": "diagnose", "diagnostics": run_diagnostics()}

    try:
        prefix = validate_pattern(job_input.get("prefix"), "prefix")
        suffix = validate_pattern(job_input.get("suffix"), "suffix")
    except ValueError as e:
        return {"error": str(e)}

    if not prefix and not suffix:
        return {"error": "at least one of prefix/suffix must be provided"}

    matches_wanted = job_input.get("matches_wanted", DEFAULT_MATCHES_WANTED)
    if not isinstance(matches_wanted, int) or matches_wanted < 1 or matches_wanted > MAX_MATCHES_WANTED:
        return {"error": f"matches_wanted must be an integer between 1 and {MAX_MATCHES_WANTED}"}

    timeout = job_input.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > MAX_TIMEOUT:
        return {"error": f"timeout must be a number between 1 and {MAX_TIMEOUT} seconds"}

    pattern = build_matching_pattern(prefix, suffix)
    effective_prefix_count = (len(prefix) + 1) if (prefix and not prefix.startswith("T")) else len(prefix)

    with tempfile.TemporaryDirectory() as tmp_dir:
        matching_file = os.path.join(tmp_dir, "matching.txt")
        result_file = os.path.join(tmp_dir, "result.txt")

        with open(matching_file, "w", encoding="utf-8") as f:
            f.write(pattern + "\n")

        cmd = [
            BINARY_PATH,
            "--matching", matching_file,
            "--prefix-count", str(effective_prefix_count),
            "--suffix-count", str(len(suffix)),
            "--quit-count", str(matches_wanted),
            "--output", result_file,
        ]

        start_time = time.time()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            stdout, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            matches = parse_result_file(result_file)
            return {
                "status": "timeout",
                "elapsed_seconds": round(time.time() - start_time, 2),
                "matches": verify_matches(matches, prefix, suffix),
            }

        elapsed = round(time.time() - start_time, 2)
        matches = parse_result_file(result_file)

        if not matches:
            return {
                "status": "no_match",
                "elapsed_seconds": elapsed,
                "engine_output": stdout[-4000:] if stdout else "",
            }

        return {
            "status": "success",
            "elapsed_seconds": elapsed,
            "matches": verify_matches(matches, prefix, suffix),
        }


def verify_matches(matches, prefix, suffix):
    verified = []
    for match in matches:
        entry = dict(match)
        try:
            derived_address = derive_tron_address(match["private_key"])
            entry["verified"] = derived_address == match["address"]
        except Exception as e:
            entry["verified"] = False
            entry["verify_error"] = str(e)
        if prefix:
            entry["prefix_ok"] = entry["address"].startswith(prefix) if "address" in entry else False
        if suffix:
            entry["suffix_ok"] = entry["address"].endswith(suffix) if "address" in entry else False
        verified.append(entry)
    return verified


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
