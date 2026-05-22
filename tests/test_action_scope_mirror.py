"""Mirror-sync test: Helm action_scope vs OpenClaw action_scope_gate.

Symmetric to ``~/.openclaw/workspace/tests/test_action_scope_gate.py``'s
``HelmMirrorSyncTests``. Helm is the primary implementation; OpenClaw
runs an independently maintained mirror at
``~/.openclaw/workspace/scripts/action_scope_gate.py``. The two files
are hand-maintained, so without an enforcement test a new verb added
on one side can silently diverge from the other — the exact symptom
R0 Important #7 was meant to eliminate.

The test is skipped gracefully when the OpenClaw checkout is absent
(clean Helm checkout, CI sandboxes). When both modules are present the
core invariants must be identical:

* ``_KO_VERBS`` (Korean verb table, per ActionScopeKind)
* ``_EN_VERBS`` (English verb table, per ActionScopeKind)
* ``MUTABLE_RESOURCES`` keys and the schema-significant fields
  (``verbs_required``, ``needs_live_source``)
* ``_LIVE_SOURCE_TOPICS``
* ``_SCOPE_RANK``

Helm's ``MUTABLE_RESOURCES`` entries are typed via
:class:`MutableResourceMeta`; OpenClaw's mirror stores them as plain
``dict[str, object]``. The comparison is on the schema-significant
fields, not the structural type, so this asymmetry is tolerated.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


OPENCLAW_SCRIPTS = Path.home() / ".openclaw" / "workspace" / "scripts"


class HelmOpenClawMirrorTests(unittest.TestCase):
    """Symmetric counterpart to OpenClaw's HelmMirrorSyncTests."""

    def setUp(self) -> None:
        mirror = OPENCLAW_SCRIPTS / "action_scope_gate.py"
        if not mirror.exists():
            self.skipTest(
                f"OpenClaw mirror not present at {mirror}; skip in clean checkout."
            )
        if str(OPENCLAW_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(OPENCLAW_SCRIPTS))
        # Import OpenClaw under a fresh module name to avoid name collisions
        # with Helm's ``action_scope`` module.
        self._oc = importlib.import_module("action_scope_gate")
        from scripts import action_scope as helm_module  # noqa: WPS433

        self._helm = helm_module

    def test_ko_verbs_identical(self) -> None:
        openclaw = {k.value: set(v) for k, v in self._oc._KO_VERBS.items()}
        helm = {k.value: set(v) for k, v in self._helm._KO_VERBS.items()}
        self.assertEqual(
            openclaw.keys(),
            helm.keys(),
            "ActionScopeKind keys differ between Helm and OpenClaw "
            "_KO_VERBS tables.",
        )
        for scope, verbs in openclaw.items():
            self.assertEqual(
                verbs, helm[scope], f"KO verb drift on scope={scope}"
            )

    def test_en_verbs_identical(self) -> None:
        openclaw = {k.value: set(v) for k, v in self._oc._EN_VERBS.items()}
        helm = {k.value: set(v) for k, v in self._helm._EN_VERBS.items()}
        self.assertEqual(openclaw, helm, "EN verb drift between Helm and OpenClaw")

    def test_mutable_resources_keys_identical(self) -> None:
        oc_keys = set(self._oc.MUTABLE_RESOURCES.keys())
        helm_keys = set(self._helm.MUTABLE_RESOURCES.keys())
        self.assertEqual(oc_keys, helm_keys, "MUTABLE_RESOURCES key drift")

    def test_mutable_resources_schema_fields_identical(self) -> None:
        for key, oc_meta in self._oc.MUTABLE_RESOURCES.items():
            helm_meta = self._helm.MUTABLE_RESOURCES[key]
            oc_verbs = {v.value for v in oc_meta["verbs_required"]}
            helm_verbs = {v.value for v in helm_meta["verbs_required"]}
            self.assertEqual(
                oc_verbs, helm_verbs, f"verbs_required drift on {key}"
            )
            self.assertEqual(
                oc_meta.get("needs_live_source"),
                helm_meta.get("needs_live_source"),
                f"needs_live_source drift on {key}",
            )

    def test_live_source_topics_identical(self) -> None:
        self.assertEqual(
            set(self._oc._LIVE_SOURCE_TOPICS),
            set(self._helm._LIVE_SOURCE_TOPICS),
            "_LIVE_SOURCE_TOPICS drift",
        )

    def test_scope_rank_identical(self) -> None:
        oc = {k.value: v for k, v in self._oc._SCOPE_RANK.items()}
        helm = {k.value: v for k, v in self._helm._SCOPE_RANK.items()}
        self.assertEqual(oc, helm, "_SCOPE_RANK drift")


if __name__ == "__main__":
    unittest.main()
