"""The primer-presence axis: a permanently separate signal from manufacturer
class (PI-VISION-010, ADR-0013, SAF-09).

`requires_operator_confirmation` is the single, uncircumventable answer to
"may this case be acted on without asking a person first" on this axis
alone. It takes no configuration parameter, by design - there is no
argument that could ever be passed to bypass it, the same structural
non-grant `appliance/web/src/lib/server/auth/capabilities.ts` already uses
for `protocol.direct`: no role is ever granted it, so there is nothing to
misconfigure. `cs71vision.classifier`/`cs71vision.api` are this module's
only callers today; PI-VISION-008's future autonomy policy must consult it
before ever attempting an autonomous sort, alongside (not instead of) its
own separate manufacturer-class confidence gate.

Today, `primer_present` is always `None` everywhere in this codebase:
nothing here has ever seen a labeled primer example, so nothing may ever
claim confident clearance. This matches `security-and-safety.md`'s
SAF-07/SAF-08 posture - this axis is not "closed" by software evidence, and
no code path fabricates a `False` reading.
"""

from __future__ import annotations


def requires_operator_confirmation(primer_present: bool | None) -> bool:
    """Whether a person must explicitly confirm before this case may be acted on.

    True for both an actual primer flag (`True`) and an unknown/ambiguous
    reading (`None`) - only a confidently clear reading (`False`) may ever
    return `False`, and no component in this codebase produces one yet.
    """
    return primer_present is not False
