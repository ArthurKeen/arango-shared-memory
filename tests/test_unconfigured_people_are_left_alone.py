"""Issue #19: the hooks must do nothing for someone who never opted in.

These hooks run for whoever opens Claude Code or Cursor in a bootstrapped project,
configured or not. For an unconfigured teammate that meant a SessionStart digest that
announced its own failure every single session, and -- after one Python edit, or just
uncommitted work the Stop-time reconciler noticed -- a Stop gate returning
``{"decision": "block"}`` every turn, clearable only by a ``/prd-sync`` that was never
set up for them. Nothing was broken: the hooks worked exactly as designed, for a person
who never asked for them.

The fix is two doors, in front of every hook on both runtimes:

  * ``SHARED_MEMORY_HOOKS=0`` -- off, for a person who IS configured and wants quiet;
  * no ``ARANGO_HOSTS`` and no ``arangodb-memory-mcp`` in a user-level MCP config --
    silence, for a person who never configured anything.

Every test here pairs a silence assertion with a POSITIVE CONTROL: the same project,
the same command, configured, doing the thing the guard suppresses. Without the pair, a
hook that stopped working for an unrelated reason would make this file pass.

Stdlib-only and source-level like the other guards here: CI installs no dependencies.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.join(REPO, "templates", ".claude", "settings.json")
HOOKS = os.path.join(REPO, "templates", ".claude", "hooks")
CURSOR_HOOKS = os.path.join(REPO, "templates", ".cursor", "hooks")
CURSOR_HOOK_FILES = (
    "shared_memory_session_start.py",
    "shared_memory_drift_queue.py",
    "shared_memory_apply_tracker.py",
    "shared_memory_stop_gate.py",
)

#: Refused on every machine, so no test here can reach a real database.
NOWHERE = "http://127.0.0.1:9"


def _commands():
    with open(SETTINGS, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = []
    for event, groups in (doc.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks") or []:
                out.append((event, h["command"]))
    return out


def _script_of(command):
    return command.split("f=.claude/hooks/", 1)[1].split(";", 1)[0]


class _Project:
    """A bootstrapped project with queued drift -- the state that blocks a stop."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.root = os.path.join(self.dir, "proj")
        self.home = os.path.join(self.dir, "home")
        os.makedirs(self.home)
        shutil.copytree(HOOKS, os.path.join(self.root, ".claude", "hooks"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        # session_recall.py returns 0 without a PROJECT_ID, so without this the
        # SessionStart positive control would be silent for the wrong reason.
        with open(os.path.join(self.root, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write("# PROJECT: guard test\n\n- PROJECT_ID: guard-test\n"
                     "- PRD_FILE: docs/PRD.md\n")
        os.makedirs(os.path.join(self.root, ".prd-drift-queue"))
        with open(os.path.join(self.root, ".prd-drift-queue", "marker.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"file": "app.py"}, fh)
        return self

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)

    def configure(self, where=".claude.json", body=None):
        """Write a user-level MCP config naming the server."""
        path = os.path.join(self.home, where)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if body is None:
            body = {"mcpServers": {"arangodb-memory-mcp": {"command": "uvx",
                                                           "env": {}}}}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2)

    def run(self, command, **env):
        base = dict(os.environ)
        for key in ("ARANGO_HOSTS", "SHARED_MEMORY_HOOKS", "ARANGO_USERNAME",
                    "ARANGO_PASSWORD"):
            base.pop(key, None)
        base["HOME"] = self.home
        base["CLAUDE_PROJECT_DIR"] = self.root
        base.update({k: v for k, v in env.items() if v is not None})
        return subprocess.run(["sh", "-c", command], cwd=self.root, env=base,
                              input="{}", capture_output=True, text=True, timeout=60)


class TestAnUnconfiguredPersonIsNeverTouched(unittest.TestCase):
    def setUp(self):
        self.commands = _commands()
        self.assertEqual(len(self.commands), 5,
                         "settings.json no longer has the five canonical hook "
                         "commands -- this file would guard the wrong thing")

    def test_every_command_exits_zero_and_says_nothing(self):
        with _Project() as p:
            for event, cmd in self.commands:
                with self.subTest(event=event, script=_script_of(cmd)):
                    r = p.run(cmd)
                    self.assertEqual(r.returncode, 0, r.stderr[:400])
                    self.assertEqual(r.stdout, "",
                                     f"{event} spoke to someone who never configured "
                                     f"shared memory: {r.stdout[:300]}")
                    self.assertEqual(r.stderr, "", r.stderr[:400])

    def test_the_stop_gate_really_would_have_blocked_them(self):
        """Positive control. Without this the silence above proves nothing."""
        with _Project() as p:
            gate = next(c for _, c in self.commands if "drift_stop_gate" in c)
            r = p.run(gate, ARANGO_HOSTS=NOWHERE)
            self.assertEqual(r.returncode, 0, r.stderr[:400])
            self.assertIn('"block"', r.stdout,
                          "the Stop gate no longer blocks on a queued marker, so the "
                          "silence test above is not suppressing anything")

    def test_the_digest_really_would_have_spoken_to_them(self):
        """Positive control for SessionStart: it announces its own failure."""
        with _Project() as p:
            recall = next(c for _, c in self.commands if "session_recall" in c)
            r = p.run(recall, ARANGO_HOSTS=NOWHERE)
            self.assertEqual(r.returncode, 0, r.stderr[:400])
            self.assertTrue(r.stdout.strip(),
                            "session_recall.py printed nothing even when configured, "
                            "so its silence test proves nothing")


class TestTheOptOutVariable(unittest.TestCase):
    def setUp(self):
        self.commands = _commands()

    def test_zero_silences_someone_who_is_configured(self):
        with _Project() as p:
            p.configure()
            for event, cmd in self.commands:
                with self.subTest(event=event, script=_script_of(cmd)):
                    r = p.run(cmd, ARANGO_HOSTS=NOWHERE, SHARED_MEMORY_HOOKS="0")
                    self.assertEqual(r.returncode, 0, r.stderr[:400])
                    self.assertEqual(r.stdout, "", r.stdout[:300])
                    self.assertEqual(r.stderr, "", r.stderr[:400])

    def test_one_leaves_a_configured_person_unchanged(self):
        with _Project() as p:
            gate = next(c for _, c in self.commands if "drift_stop_gate" in c)
            r = p.run(gate, ARANGO_HOSTS=NOWHERE, SHARED_MEMORY_HOOKS="1")
            self.assertIn('"block"', r.stdout,
                          "SHARED_MEMORY_HOOKS=1 turned the gate off; only 0 may")

    def test_unset_leaves_a_configured_person_unchanged(self):
        with _Project() as p:
            gate = next(c for _, c in self.commands if "drift_stop_gate" in c)
            r = p.run(gate, ARANGO_HOSTS=NOWHERE)
            self.assertIn('"block"', r.stdout,
                          "the hooks became opt-in by accident -- unset must mean on "
                          "for a configured person")


class TestWhatCountsAsConfigured(unittest.TestCase):
    def setUp(self):
        self.gate = next(c for _, c in _commands() if "drift_stop_gate" in c)

    def test_a_server_entry_in_claude_json_counts(self):
        with _Project() as p:
            p.configure(".claude.json")
            self.assertIn('"block"', p.run(self.gate).stdout,
                          "a configured user was treated as unconfigured")

    def test_a_server_entry_in_cursor_mcp_json_counts(self):
        with _Project() as p:
            p.configure(".cursor/mcp.json")
            self.assertIn('"block"', p.run(self.gate).stdout,
                          "the Cursor MCP config is not being consulted")

    def test_the_name_in_a_permission_allowlist_is_not_configuration(self):
        """Allowing a tool is not configuring the server that provides it."""
        with _Project() as p:
            p.configure(".claude.json", body={"permissions": {"allow": [
                "mcp__arangodb-memory-mcp__pattern-search",
                "Bash(uvx arangodb-memory-mcp *)",
            ]}})
            r = p.run(self.gate)
            self.assertEqual(r.stdout, "",
                             "a permission allowlist entry was read as configuration")

    def test_an_unreadable_config_is_not_configuration(self):
        with _Project() as p:
            with open(os.path.join(p.home, ".claude.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{ this is not json")
            r = p.run(self.gate)
            self.assertEqual(r.returncode, 0, r.stderr[:400])


class TestTheGuardRunsUnderEveryShell(unittest.TestCase):
    """settings.json does not choose the shell. A bashism here breaks every hook."""

    def _guard(self):
        cmd = _commands()[0][1]
        prefix = cmd.split("f=.claude/hooks/", 1)[0]
        self.assertIn("SHARED_MEMORY_HOOKS", prefix,
                      "no guard found in front of the hook command")
        return prefix

    def test_each_available_shell_accepts_it(self):
        guard = self._guard()
        shells = [s for s in ("sh", "bash", "dash", "zsh") if shutil.which(s)]
        self.assertGreaterEqual(len(shells), 2, f"only found {shells}")
        with tempfile.TemporaryDirectory() as home:
            for shell in shells:
                for configured in (True, False):
                    env = dict(os.environ, HOME=home)
                    env.pop("SHARED_MEMORY_HOOKS", None)
                    if configured:
                        env["ARANGO_HOSTS"] = NOWHERE
                    else:
                        env.pop("ARANGO_HOSTS", None)
                    with self.subTest(shell=shell, configured=configured):
                        r = subprocess.run(
                            [shell, "-c", guard + "echo REACHED"], env=env,
                            stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=30)
                        self.assertEqual(r.returncode, 0, r.stderr[:300])
                        self.assertEqual(r.stderr, "", r.stderr[:300])
                        self.assertEqual("REACHED" in r.stdout, configured,
                                         f"{shell}: guard let the wrong case through")

    def test_every_command_carries_it(self):
        for event, cmd in _commands():
            with self.subTest(event=event, script=_script_of(cmd)):
                self.assertLess(cmd.index("SHARED_MEMORY_HOOKS"),
                                cmd.index("f=.claude/hooks/"),
                                "the guard must come before the dispatch, or the "
                                "hook has already run by the time it is consulted")
                self.assertIn("arangodb-memory-mcp", cmd)


def _cursor_module(name):
    path = os.path.join(CURSOR_HOOKS, name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheCursorHooksHaveTheSameDoors(unittest.TestCase):
    """Cursor hook files are copied verbatim into every bootstrapped project by
    scripts/rollout_cursor_hooks.py, so the same person gets the same nagging there."""

    def _run(self, name, project, payload=None, **env):
        base = dict(os.environ)
        for key in ("ARANGO_HOSTS", "SHARED_MEMORY_HOOKS"):
            base.pop(key, None)
        base["HOME"] = project.home
        base.update({k: v for k, v in env.items() if v is not None})
        return subprocess.run(["python3", os.path.join(CURSOR_HOOKS, name)],
                              cwd=project.root, env=base,
                              input=json.dumps(payload or {}), capture_output=True,
                              text=True, timeout=30)

    #: Payloads that make each hook do its real work, so the silence below is the
    #: guard suppressing something rather than an empty fixture saying nothing.
    LIVE = {
        "shared_memory_session_start.py": {},
        "shared_memory_drift_queue.py": {"file_path": "app.py"},
        "shared_memory_apply_tracker.py": {
            "conversation_id": "c1", "tool_name": "MCP:pattern-search",
            "tool_response": {"results": [{"_key": "p1"}]}},
        "shared_memory_stop_gate.py": {"conversation_id": "c1", "loop_count": 0},
    }

    def test_each_hook_is_silent_for_an_unconfigured_person(self):
        with _Project() as p:
            for name in CURSOR_HOOK_FILES:
                with self.subTest(hook=name):
                    r = self._run(name, p, self.LIVE[name])
                    self.assertEqual(r.returncode, 0, r.stderr[:400])
                    self.assertEqual(r.stdout.strip(), "{}", r.stdout[:300])

    def test_the_stop_gate_really_would_have_nagged_them(self):
        """Positive control: the same project, the same payload, configured."""
        with _Project() as p:
            r = self._run("shared_memory_stop_gate.py", p,
                          self.LIVE["shared_memory_stop_gate.py"],
                          ARANGO_HOSTS=NOWHERE)
            self.assertEqual(r.returncode, 0, r.stderr[:400])
            self.assertIn("PRD-DRIFT GATE", r.stdout,
                          "the Cursor stop gate no longer nags on a queued marker, "
                          "so its silence test is not suppressing anything")

    def test_the_drift_queue_hook_really_would_have_queued(self):
        """Positive control: an edit queues a marker only when configured."""
        marker_dir = os.path.join(".prd-drift-queue")
        with _Project() as p:
            before = len(os.listdir(os.path.join(p.root, marker_dir)))
            self._run("shared_memory_drift_queue.py", p,
                      self.LIVE["shared_memory_drift_queue.py"])
            self.assertEqual(len(os.listdir(os.path.join(p.root, marker_dir))), before,
                             "an unconfigured person's edit still queued drift")
            self._run("shared_memory_drift_queue.py", p,
                      self.LIVE["shared_memory_drift_queue.py"],
                      ARANGO_HOSTS=NOWHERE)
            self.assertGreater(len(os.listdir(os.path.join(p.root, marker_dir))),
                               before,
                               "the Cursor drift-queue hook queues nothing even when "
                               "configured, so its silence test proves nothing")

    def test_each_hook_exposes_the_same_two_doors(self):
        for name in CURSOR_HOOK_FILES:
            with self.subTest(hook=name):
                configured = _cursor_module(name).shared_memory_configured
                with tempfile.TemporaryDirectory() as home:
                    env = {"HOME": home}
                    with _patched_env(env, ARANGO_HOSTS=None,
                                      SHARED_MEMORY_HOOKS=None):
                        self.assertFalse(configured())
                    with _patched_env(env, ARANGO_HOSTS=NOWHERE,
                                      SHARED_MEMORY_HOOKS=None):
                        self.assertTrue(configured())
                    with _patched_env(env, ARANGO_HOSTS=NOWHERE,
                                      SHARED_MEMORY_HOOKS="0"):
                        self.assertFalse(configured(),
                                         "SHARED_MEMORY_HOOKS=0 did not turn it off")

    def test_a_config_file_counts_and_an_allowlist_does_not(self):
        configured = _cursor_module(CURSOR_HOOK_FILES[0]).shared_memory_configured
        cases = [
            (".claude.json",
             {"mcpServers": {"arangodb-memory-mcp": {"command": "uvx"}}}, True),
            (".claude.json",
             {"projects": {"/x": {"mcpServers":
                                  {"arangodb-memory-mcp": {"command": "uvx"}}}}},
             True),
            (".cursor/mcp.json",
             {"mcpServers": {"arangodb-memory-mcp": {"command": "uvx"}}}, True),
            (".claude.json",
             {"permissions": {"allow":
                              ["mcp__arangodb-memory-mcp__pattern-search"]}}, False),
            (".claude.json", {"mcpServers": {"something-else": {}}}, False),
        ]
        for where, body, expected in cases:
            with self.subTest(where=where, expected=expected):
                with tempfile.TemporaryDirectory() as home:
                    path = os.path.join(home, where)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as fh:
                        json.dump(body, fh, indent=2)
                    with _patched_env({"HOME": home}, ARANGO_HOSTS=None,
                                      SHARED_MEMORY_HOOKS=None):
                        self.assertIs(configured(), expected)

    def test_the_guard_is_the_first_thing_main_does(self):
        """A guard that runs after the work has been done is not a guard."""
        for name in CURSOR_HOOK_FILES:
            with self.subTest(hook=name):
                with open(os.path.join(CURSOR_HOOKS, name), encoding="utf-8") as fh:
                    body = fh.read()
                after = body.split("def main() -> int:", 1)[1]
                first = next(line.strip() for line in after.splitlines()
                             if line.strip() and not line.strip().startswith('"""'))
                self.assertEqual(first, "if not shared_memory_configured():",
                                 f"{name} does something before consulting the guard")


class _patched_env:
    def __init__(self, base, **overrides):
        self.values = dict(base)
        self.values.update(overrides)

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.values}
        for key, value in self.values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def __exit__(self, *_):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestTheRolloutCarriesTheGuard(unittest.TestCase):
    """The 32 projects that lack the guard only get it if the rollout rewrites the
    commands they already have."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "rollout", os.path.join(REPO, "scripts", "rollout_cursor_hooks.py"))
        self.rollout = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.rollout)
        with open(SETTINGS, encoding="utf-8") as fh:
            self.template = json.load(fh)

    def _unguarded(self):
        """A project's settings.json as it stands today: the current commands with
        the guard stripped off the front."""
        doc = json.loads(json.dumps(self.template))
        for groups in doc["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    hook["command"] = "f=.claude/hooks/" + \
                        hook["command"].split("f=.claude/hooks/", 1)[1]
        doc["permissions"] = {"allow": ["Bash(ls *)"]}
        return doc

    def test_an_existing_project_gets_the_guard(self):
        existing = self._unguarded()
        for groups in existing["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertNotIn("SHARED_MEMORY_HOOKS", hook["command"],
                                     "the fixture is already guarded, so this test "
                                     "would pass without the rollout doing anything")
        merged = self.rollout.merged_claude_settings(existing, self.template)
        commands = [h["command"] for gs in merged["hooks"].values()
                    for g in gs for h in g["hooks"]]
        self.assertEqual(len(commands), 5)
        for command in commands:
            self.assertIn("SHARED_MEMORY_HOOKS", command,
                          "a rolled-out project keeps running unguarded hooks")
        self.assertEqual(merged["permissions"], {"allow": ["Bash(ls *)"]},
                         "the rollout overwrote settings it does not own")


if __name__ == "__main__":
    unittest.main()
