"""Synthetic offline descriptors only; not site inventory or execution proof."""
import base64
import copy
import hashlib
import importlib.util
import pathlib
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / "scripts/isolated_validation_plan.py"
spec = importlib.util.spec_from_file_location("offline_plan", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    def blob(path, data=b'{"synthetic":true}'):
        return dict(path=path, content_base64=base64.b64encode(data).decode(),
                    sha256=hashlib.sha256(data).hexdigest())

    def path(value, role=None):
        return dict(path=value, resolved_path=value, attested_by="synthetic-test",
                    observed_at="2026-10-21T00:00:00Z", role=role)

    services = []
    for i in (1, 2):
        root = f"/original-{i}"
        services.append(dict(
            inventory_complete=True, service_id=f"service-{i}", source_commit="1"*40, source_tree="2"*40,
            config=blob(root + "/config"), node_map=blob(root + "/node_map"),
            model_identity="synthetic-model-sha", steering_identity="synthetic-steering-sha",
            restore_entrypoint_id="registered-stop-start-v1", restore_evidence_id="evidence-restore",
            readiness_evidence_id="evidence-ready", capacity={"tokens": 1}, guards={"enabled": True},
            restore_preserves_original_files=True,
            restore_authority=dict(policy=blob(root + "/policy"), config=blob(root + "/config"),
                                   controller_identity="sha256:" + "a"*64,
                                   registration_evidence_id="registered-before-test", expected_transition="none"),
            nodes=[dict(node_id="synthetic-node", container_id=str(i)*64,
                        image_id=str(i)*64, container_name=f"original-{i}", image_tag=f"original:{i}",
                        original_state="running", container_preserved=True, ports=[8000+i],
                        protected_paths=[path(root + "/" + role, role) for role in
                                         ("source", "config", "node_map", "model", "steering", "cache", "policy", "controller")])]))
    return dict(schema_version=1, inventory_id="synthetic", responsible_operator="test-only",
                candidate=dict(fork_url="https://github.com/imDaniel/spark-ds41f",
                               ref="refs/heads/synthetic-test", commit="3"*40, tree="4"*40,
                               runner_head="3"*40, fetched_tip="3"*40, fetch_succeeded=True,
                               clean=True, approval_evidence_id="synthetic-approval", fetch_evidence_id="synthetic-fetch", fetched_at="2026-10-21T00:00:00Z"),
                services=services,
                candidate_nodes=[dict(node_id="synthetic-node", commit="3"*40, tree="4"*40,
                                      container_name="candidate", image_tag="candidate:immutable",
                                      ports=[9000], checkout_paths=[path("/candidate/source")],
                                      cache_paths=[path("/candidate/cache")], output_paths=[path("/candidate/output")])])


class OfflinePlanTests(unittest.TestCase):
    def test_complete_synthetic_descriptor(self):
        self.assertEqual(module.validate(fixture()), [])

    def reject(self, mutate, substring):
        plan = fixture()
        mutate(plan)
        self.assertTrue(any(substring in e for e in module.validate(plan)))

    def test_requires_two_services(self):
        self.reject(lambda p: p["services"].pop(), "TWO")

    def test_wrong_owner_project(self):
        self.reject(lambda p: p["candidate"].update(
            fork_url="https://github.com/imDaniel/unrelated"), "selected imDaniel/spark-ds41f")

    def test_publication_mismatch(self):
        self.reject(lambda p: p["candidate"].update(runner_head="9"*40), "frozen commit")

    def test_empty_ref_suffix(self):
        for ref in ("refs/heads/", "refs/heads/bad\nref", "refs/heads/x/",
                    "refs/heads/x.", "refs/heads/x.lock", "refs/heads/.hidden"):
            with self.subTest(ref=ref):
                self.reject(lambda p: p["candidate"].update(ref=ref), "full branch ref")

    def test_duplicate_original_container(self):
        for key in ("container_id", "container_name"):
            with self.subTest(key=key):
                self.reject(lambda p: p["services"][1]["nodes"][0].update(
                    {key: p["services"][0]["nodes"][0][key]}), "same container")

    def test_fetch_failure(self):
        self.reject(lambda p: p["candidate"].update(fetch_succeeded=False), "fetch_succeeded")

    def test_dirty(self):
        self.reject(lambda p: p["candidate"].update(clean=False), "candidate.clean")

    def test_byte_hash_mismatch(self):
        self.reject(lambda p: p["services"][0]["config"].update(sha256="f"*64), "match SHA256")

    def test_symlink_attestation_collision(self):
        self.reject(lambda p: p["candidate_nodes"][0]["cache_paths"][0].update(resolved_path="/original-1/cache/nested"), "overlaps protected")

    def test_lexical_parent_collision(self):
        self.reject(lambda p: p["candidate_nodes"][0]["checkout_paths"][0].update(path="/original-1"), "overlaps protected")

    def test_missing_attestation(self):
        self.reject(lambda p: p["candidate_nodes"][0]["cache_paths"][0].pop("attested_by"), "attested_by")

    def test_candidate_internal_nesting_is_allowed(self):
        plan = fixture()
        node = plan["candidate_nodes"][0]
        for role in ("cache", "output"):
            node[role + "_paths"][0].update(
                path="/candidate/source/.work/" + role,
                resolved_path="/candidate/source/.work/" + role)
        self.assertEqual(module.validate(plan), [])

    def test_evidenced_non_git_no_steering_service(self):
        plan = fixture()
        service = plan["services"][1]
        service.pop("source_commit")
        service.pop("source_tree")
        service.pop("steering_identity")
        service.update(source_kind="artifact", source_artifact_sha256="e"*64,
                       git_not_applicable={"reason": "immutable package, no checkout", "evidence_id": "inventory-2"},
                       steering_not_applicable={"reason": "no steering capability", "evidence_id": "inventory-2"})
        entries = service["nodes"][0]["protected_paths"]
        entries[:] = [e for e in entries if e["role"] != "steering"]
        next(e for e in entries if e["role"] == "source")["role"] = "artifact"
        self.assertEqual(module.validate(plan), [])
        service["git_not_applicable"].pop("evidence_id")
        self.assertTrue(any("evidence_id" in e for e in module.validate(plan)))

    def test_missing_steering_is_not_assumed_inapplicable(self):
        self.reject(lambda p: p["services"][1].pop("steering_identity"), "steering_identity")

    def test_reserved_names_tags_ports(self):
        for key, value in (("container_name", "original-1"), ("image_tag", "original:1"), ("ports", [8001])):
            with self.subTest(key=key):
                self.reject(lambda p: p["candidate_nodes"][0].update({key: value}), "colli")

    def test_rejects_embedded_commands(self):
        self.reject(lambda p: p["services"][0].update(command="echo unsafe"), "command fields")
        self.reject(lambda p: p["services"][0].update(restore_entrypoint_id="sh -c anything"), "never shell")

    def test_complete_inventory_attestation_required(self):
        self.reject(lambda p: p["services"][0].pop("inventory_complete"), "complete service")

    def test_unknown_node(self):
        self.reject(lambda p: p["candidate_nodes"][0].update(node_id="unknown"), "inventoried")

    def test_unprotected_restore_config(self):
        self.reject(lambda p: p["services"][0]["restore_authority"]["config"].update(path="/elsewhere/config"), "not covered")

    def test_pending_registered_policy_is_separate(self):
        plan = fixture()
        authority = plan["services"][0]["restore_authority"]
        data = b'{"synthetic_pending_change":true}'
        authority["config"].update(content_base64=base64.b64encode(data).decode(),
                                   sha256=hashlib.sha256(data).hexdigest())
        self.assertTrue(any("different restore config" in e for e in module.validate(plan)))
        authority.update(expected_transition="registered-pending-policy", expected_change="preexisting details flag",
                         exclude_from_candidate_benefit=True)
        self.assertEqual(module.validate(plan), [])

    def test_malformed_inputs_return_deficiencies(self):
        for value in (None, [], {}, {"candidate": None}):
            self.assertTrue(module.validate(value))


class AbsentServiceTests(unittest.TestCase):
    def absent_plan(self):
        plan = fixture()
        service = plan["services"][1]
        service["service_id"] = "synthetic-glm"
        original = service["nodes"][0]
        original.pop("container_id")
        original.pop("container_preserved")
        original.update(original_state="absent", restore_state="absent",
                        absence_evidence={"exists": False, "running": False,
                                          "attested_by": "synthetic-operator",
                                          "observed_at": "2026-10-21T00:00:00Z",
                                          "evidence_id": "synthetic-absent-inventory"})
        # Model the reported four-node shape without using any real site data.
        for item in plan["services"]:
            item["nodes"] = [dict(copy.deepcopy(item["nodes"][0]), node_id=f"node-{i}")
                             for i in range(4)]
        plan["candidate_nodes"] = [dict(copy.deepcopy(plan["candidate_nodes"][0]),
                                        node_id=f"node-{i}") for i in range(4)]
        return plan

    def test_four_nodes_attested_absent_need_no_invented_ids(self):
        plan = self.absent_plan()
        self.assertEqual(module.validate(plan), [])
        self.assertTrue(all("container_id" not in row for row in plan["services"][1]["nodes"]))

    def test_absent_observation_requires_strict_booleans_and_provenance(self):
        for key in ("exists", "running"):
            for value in (True, None, 0, "false"):
                plan = self.absent_plan()
                plan["services"][1]["nodes"][0]["absence_evidence"][key] = value
                with self.subTest(key=key, value=value):
                    self.assertTrue(module.validate(plan))
        for key in ("attested_by", "observed_at", "evidence_id"):
            plan = self.absent_plan()
            plan["services"][1]["nodes"][0]["absence_evidence"].pop(key)
            with self.subTest(key=key):
                self.assertTrue(any(key in error for error in module.validate(plan)))
        plan = self.absent_plan()
        plan["services"][1]["nodes"][0].pop("absence_evidence")
        self.assertTrue(module.validate(plan))

    def test_absent_rejects_present_identity_running_or_restore_start(self):
        for key, value in (("container_id", "a" * 64), ("container_preserved", True),
                           ("exists", True), ("running", True),
                           ("restore_state", "running"), ("restore_state", "stopped")):
            plan = self.absent_plan()
            plan["services"][1]["nodes"][0][key] = value
            with self.subTest(key=key, value=value):
                self.assertTrue(module.validate(plan))

    def test_absent_still_requires_registered_image_and_every_protected_role(self):
        plan = self.absent_plan()
        plan["services"][1]["nodes"][0].pop("image_id")
        self.assertTrue(any("image_id" in error for error in module.validate(plan)))
        for role in ("source", "config", "node_map", "model", "steering", "cache", "policy", "controller"):
            plan = self.absent_plan()
            row = plan["services"][1]["nodes"][0]
            row["protected_paths"] = [item for item in row["protected_paths"] if item["role"] != role]
            with self.subTest(role=role):
                self.assertTrue(any("protected_paths" in error for error in module.validate(plan)))

    def test_absent_keeps_names_tags_ports_and_paths_reserved(self):
        for key, value in (("container_name", "original-2"), ("image_tag", "original:2"),
                           ("ports", [8002])):
            plan = self.absent_plan()
            plan["candidate_nodes"][0][key] = value
            with self.subTest(key=key):
                self.assertTrue(any("colli" in error for error in module.validate(plan)))
        plan = self.absent_plan()
        plan["candidate_nodes"][0]["cache_paths"][0]["resolved_path"] = "/original-2/cache/new"
        self.assertTrue(any("overlaps protected" in error for error in module.validate(plan)))

    def test_absent_and_stopped_existing_are_not_interchangeable(self):
        plan = self.absent_plan()
        plan["services"][1]["nodes"][0]["original_state"] = "stopped"
        errors = module.validate(plan)
        self.assertTrue(any("container_id" in error for error in errors))
        self.assertTrue(any("container must be preserved" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
