"""Architecture validation helpers (MVP: basic checks)."""

from __future__ import annotations


def check_compilation(service_path: str) -> dict:
    """Run mvn compile and return result."""
    import subprocess

    try:
        result = subprocess.run(
            f"cd {service_path} && mvn compile -q 2>&1",
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        success = result.returncode == 0
        errors = [] if success else [line for line in (result.stdout + result.stderr).splitlines() if "ERROR" in line]
        return {"compile_success": success, "compile_errors": errors}
    except subprocess.TimeoutExpired:
        return {"compile_success": False, "compile_errors": ["Compilation timed out after 300s"]}
    except Exception as e:
        return {"compile_success": False, "compile_errors": [str(e)]}
