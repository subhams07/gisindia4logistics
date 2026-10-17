"""Policy receipt model and comparison semantics.

No numeric value in this module is an approved policy value. `synthetic_test_policy`
builds clearly labelled SYNTHETIC_TEST_POLICY_NOT_APPROVED values for self-tests only.

Comparison semantics (documented, unit-tested):
  floor field: observed must stay at or above the floor.
      strict     -> breach iff observed <  floor
      inclusive  -> breach iff observed <= floor
  cap field: observed must stay at or below the cap.
      strict     -> breach iff observed >  cap
      inclusive  -> breach iff observed >= cap
"""
import os

from .errors import PolicyStop

SYNTHETIC_LABEL = "SYNTHETIC_TEST_POLICY_NOT_APPROVED"

# name -> (unit, kind)
FIELDS = {
    "attempt_memory_limit_bytes": ("bytes", "config"),
    "attempt_pids_max": ("count", "config"),
    "reserve_bytes": ("bytes", "config"),  # D-5 policy input
    "launch_min_effective_available_bytes": ("bytes", "floor"),
    "running_floor_effective_available_bytes": ("bytes", "floor"),
    "running_floor_attempt_headroom_bytes": ("bytes", "floor"),
    "bash_cgroup_growth_max_bytes": ("bytes", "cap"),
    "launch_min_disk_free_bytes": ("bytes", "floor"),
    "running_floor_disk_free_bytes": ("bytes", "floor"),
    "sample_interval_ms": ("ms", "config"),
    "max_sample_gap_ms": ("ms", "cap"),
    "release_sample_count_k": ("count", "config"),
    "per_slot_elapsed_cap_s": ("s", "cap"),
    "graceful_wait_s": ("s", "config"),
    "cgroup_kill_wait_s": ("s", "config"),
    "release_handshake_timeout_s": ("s", "config"),
    "cold_input_residency_max_fraction": ("fraction", "cap"),
    "unshare_reap_wait_s": ("s", "config"),
    "memsw_tolerance_bytes": ("bytes", "cap"),
    "conservative_peak_max_bytes": ("bytes", "cap"),  # D-11 gate on peak + resident_elsewhere
    "failcnt_action": ("token", "action"),
    "memsw_check_action": ("token", "action"),
}
STRICTNESS = ("strict", "inclusive", "n/a")
ACTIONS = ("record", "stop")


def breach(kind, observed, threshold, strictness):
    """True if `observed` breaches `threshold` under the documented semantics."""
    if kind == "floor":
        if strictness == "strict":
            return observed < threshold
        if strictness == "inclusive":
            return observed <= threshold
    elif kind == "cap":
        if strictness == "strict":
            return observed > threshold
        if strictness == "inclusive":
            return observed >= threshold
    raise PolicyStop("policy_bad_comparison", "%s/%s" % (kind, strictness))


def _entry(value, unit, strictness, basis):
    return {"value": value, "unit": unit, "strictness": strictness, "basis": basis}


def synthetic_test_policy(**overrides):
    """Clearly labelled synthetic values for self-tests. NOT approved, NOT a proposal."""
    mib = 1 << 20
    vals = {
        "attempt_memory_limit_bytes": 192 * mib,
        "attempt_pids_max": 48,
        "reserve_bytes": 512 * mib,
        "launch_min_effective_available_bytes": 1024 * mib,
        "running_floor_effective_available_bytes": 512 * mib,
        "running_floor_attempt_headroom_bytes": 16 * mib,
        "bash_cgroup_growth_max_bytes": 256 * mib,
        "launch_min_disk_free_bytes": 256 * mib,
        "running_floor_disk_free_bytes": 128 * mib,
        "sample_interval_ms": 50,
        "max_sample_gap_ms": 2000,
        "release_sample_count_k": 3,
        "per_slot_elapsed_cap_s": 60,
        "graceful_wait_s": 3,
        "cgroup_kill_wait_s": 5,
        "release_handshake_timeout_s": 10,
        "cold_input_residency_max_fraction": 0.5,
        "unshare_reap_wait_s": 3,
        "memsw_tolerance_bytes": 64 * (os.cpu_count() or 1) * 4096 * 4,
        "conservative_peak_max_bytes": 160 * mib,
        "failcnt_action": "stop",
        "memsw_check_action": "record",
    }
    strict = {}
    for name, (_unit, kind) in FIELDS.items():
        strict[name] = "strict" if kind in ("floor", "cap") else "n/a"
    vals.update(overrides)
    fields = {}
    for name, (unit, _kind) in FIELDS.items():
        fields[name] = _entry(vals[name], unit, strict[name], SYNTHETIC_LABEL)
    return {"label": SYNTHETIC_LABEL, "approved": False, "fields": fields}


def validate_policy(policy, nproc=None):
    """Structural validation. Raises PolicyStop. Does not make values 'approved'."""
    if not isinstance(policy, dict) or "fields" not in policy:
        raise PolicyStop("policy_malformed", "no fields")
    fields = policy["fields"]
    for name, (unit, kind) in FIELDS.items():
        if name not in fields:
            raise PolicyStop("policy_missing_field", name)
        e = fields[name]
        for key in ("value", "unit", "strictness", "basis"):
            if key not in e:
                raise PolicyStop("policy_field_incomplete", "%s.%s" % (name, key))
        if e["unit"] != unit:
            raise PolicyStop("policy_bad_unit", "%s %r != %r" % (name, e["unit"], unit))
        if e["strictness"] not in STRICTNESS:
            raise PolicyStop("policy_bad_strictness", name)
        if kind in ("floor", "cap") and e["strictness"] == "n/a":
            raise PolicyStop("policy_strictness_required", name)
        if not e["basis"]:
            raise PolicyStop("policy_basis_required", name)
        if kind == "action":
            if e["value"] not in ACTIONS:
                raise PolicyStop("policy_bad_action", name)
        elif isinstance(e["value"], bool) or not isinstance(e["value"], (int, float)):
            raise PolicyStop("policy_bad_value_type", name)
        elif e["value"] < 0:
            raise PolicyStop("policy_negative_value", name)
    extra = set(fields) - set(FIELDS)
    if extra:
        raise PolicyStop("policy_unknown_fields", ",".join(sorted(extra)))
    page = os.sysconf("SC_PAGE_SIZE")
    if fields["attempt_memory_limit_bytes"]["value"] % page:
        raise PolicyStop("policy_limit_not_page_aligned", "attempt_memory_limit_bytes")
    n = nproc or (os.cpu_count() or 1)
    if fields["memsw_tolerance_bytes"]["value"] < 64 * n * 4096:
        raise PolicyStop("policy_memsw_tolerance_below_minimum", "")
    if fields["failcnt_action"]["value"] != "stop":
        # D-11 ruling: failcnt_action = STOP
        raise PolicyStop("policy_failcnt_action_must_be_stop", "D-11")
    if fields["release_sample_count_k"]["value"] < 1:
        raise PolicyStop("policy_k_below_one", "")
    return True


def value(policy, name):
    return policy["fields"][name]["value"]


def check(policy, name, observed):
    """Evaluate a floor/cap field. Returns True if breached."""
    _unit, kind = FIELDS[name]
    e = policy["fields"][name]
    return breach(kind, observed, e["value"], e["strictness"])
