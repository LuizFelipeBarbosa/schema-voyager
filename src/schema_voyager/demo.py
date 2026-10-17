"""Command line: run the demo sweep, single episodes, and arena helpers.

    schema-voyager demo --backend mc            # train T1-T3, then H3 for D and B
    schema-voyager demo --from runs/<run> --second-visit   # that run's training, a fresh H3, D's revisit
    schema-voyager episode --system D --task h3 --layout 6 --store runs/<run>/store_D_2_after_training.json
    schema-voyager arena --layout 6 --task h3   # build a layout on the server
    schema-voyager spectate <player>            # put your client in spectator mode
    schema-voyager report runs/<run>
    schema-voyager store runs/<run>/store_D_2_after_training.json   # print a saved store
    schema-voyager panel                        # trace panel on http://127.0.0.1:8765
    schema-voyager export runs/<run>            # results/ folder and the field guide's numbers
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

from .adapter import Backend
from .agent import run_schemavoyager, seed_prior_rules
from .baseline import SkillLibrary, run_skill_only
from .episode import Episode
from .evaluator import summarize
from .layouts import load_layout
from .llm import LLM, LLMError, fixture_prior_rules
from .models import EpisodeResult, Task
from .planner import PlanningError, make_plan
from .schema_store import TRAIN_WORLD, SchemaStore
from .tasks import HOLDOUT_LAYOUTS, TRAIN_LAYOUTS, get_task, training_tasks
from .trace import EpisodeLog

LLM_CACHE = Path("runs/llm_cache.jsonl")


class Runner:
    def __init__(self, backend: Backend, llm: LLM, run_dir: Path):
        self.backend = backend
        self.llm = llm
        self.run_dir = run_dir
        self.results: list[EpisodeResult] = _load_results(run_dir)

    def run(self, system: str, task: Task, seed: int, store: SchemaStore | None = None,
            library: SkillLibrary | None = None, world_id: str | None = None,
            label: str | None = None) -> EpisodeResult:
        if world_id is not None and task.split == "train":
            raise ValueError("world_id is only valid for holdout tasks")
        episode_world = world_id
        if episode_world is None:
            episode_world = TRAIN_WORLD if task.split == "train" else self._fresh_world_id(system, task, seed)
        suffix = f"/{label}" if label else ""
        trace_suffix = f"_{label}" if label else ""
        episode_id = f"{system}/{task.task_id}/L{seed}{suffix}"
        trace_name = f"{system}_{task.task_id}_L{seed}{trace_suffix}.jsonl"
        trace_label = f"{system} {task.task_id} L{seed}" + (f" {label}" if label else "")
        log = EpisodeLog(
            self.run_dir / "episodes" / trace_name,
            label=trace_label,
            say=self.backend.say,
        )
        observation = self.backend.reset(task, load_layout(seed, self.run_dir / "layouts"))
        episode = Episode(
            episode_id=episode_id, system=system, task=task, layout_seed=seed, world_id=episode_world,
            backend=self.backend, llm=self.llm, log=log, inventory=observation.inventory,
        )
        if system == "D":
            result = run_schemavoyager(episode, store)
        else:
            fake = _fake_baseline_planner(task) if self.llm.mode == "fake" else None
            result = run_skill_only(episode, library, fake, report_effects=(system == "B+"))
        self.keep([result])
        return result

    def keep(self, results: list[EpisodeResult]) -> None:
        """Add results to this run's table, replacing earlier ones for the same episodes."""
        replaced = {result.episode_id for result in results}
        self.results = [r for r in self.results if r.episode_id not in replaced] + results
        self._write_results()

    def snapshot(self, store: SchemaStore, label: str) -> Path:
        path = self.run_dir / f"store_D_{label}.json"
        store.save(path)
        return path

    def _fresh_world_id(self, system: str, task: Task, seed: int) -> str:
        """Opaque per-episode world id: the agent is never told whether rules changed."""
        digest = hashlib.sha1(f"{self.run_dir.name}/{system}/{task.task_id}/{seed}".encode()).hexdigest()
        return f"w{digest[:5]}"

    def _write_results(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "results.json").write_text(
            json.dumps([r.to_dict() for r in self.results], indent=2) + "\n", encoding="utf-8"
        )
        (self.run_dir / "results.md").write_text(summarize(self.results), encoding="utf-8")


def run_demo(args: argparse.Namespace) -> None:
    run_dir = args.run_dir or Path("runs") / time.strftime("%Y%m%d-%H%M%S")
    _record_commit(run_dir)
    runner = Runner(make_backend(args.backend), LLM(mode=args.llm, cache_path=LLM_CACHE), run_dir)
    systems = args.systems.split(",")
    holdout_seeds = _seeds(args.holdout_layouts)
    print(f"SchemaVoyager demo: backend={args.backend} llm={args.llm} model={runner.llm.model} run_dir={run_dir}")

    if args.from_run:
        store, libraries = _reuse_training(runner, args.from_run, systems)
    else:
        store, libraries = _train(runner, systems, _seeds(args.train_layouts))

    _banner(f"Holdout H3 (coal ore needs a stone-tier pickaxe; agents are not told), layouts {holdout_seeds}")
    for seed in holdout_seeds:
        for system in systems:
            runner.run(system, get_task("h3"), seed, store=store, library=libraries.get(system))
    if "D" in systems:
        runner.snapshot(store, "3_after_holdout")
    for system, library in libraries.items():
        library.save(run_dir / f"skills_{system}_after_holdout.json")

    if args.second_visit and "D" in systems:
        seed = holdout_seeds[0]
        first = next(r for r in runner.results if r.episode_id == f"D/h3/L{seed}")
        _banner(f"Second visit: D in layout {seed}'s world {first.world_id} again")
        runner.run("D", get_task("h3"), seed, store=store, world_id=first.world_id, label="visit2")
        runner.snapshot(store, "4_after_visit2")

    _banner("Results")
    print(summarize([r for r in runner.results if r.task_id == "h3"]))
    print(f"Full results: {run_dir / 'results.md'}  Episode traces: {run_dir / 'episodes'}")


def _train(runner: Runner, systems: list[str], train_seeds: list[int]) -> tuple[SchemaStore, dict[str, SkillLibrary]]:
    """D's prior rules and training on T1-T3, then each baseline's."""
    store = SchemaStore()
    libraries = {system: SkillLibrary() for system in systems if system != "D"}
    if "D" in systems:
        _banner("Prior rules proposed by the LLM (all hypotheses)")
        seed_prior_rules(store, runner.llm)
        print(describe_store(store))
        runner.snapshot(store, "0_prior")

        _banner(f"Training D on T1-T3, layouts {train_seeds}")
        for task in training_tasks():
            for seed in train_seeds:
                runner.run("D", task, seed, store=store)
                if task.task_id == "t1" and seed == train_seeds[0]:
                    runner.snapshot(store, "1_after_first_episode")
                    print("\nAfter one episode, nothing is certified:\n" + describe_store(store))
        runner.snapshot(store, "2_after_training")
        _banner("Store after training")
        print(describe_store(store))

    for system, library in libraries.items():
        _banner(f"Training {system} (skill-only) on T1-T3, layouts {train_seeds}")
        for task in training_tasks():
            for seed in train_seeds:
                runner.run(system, task, seed, library=library)
        library.save(runner.run_dir / f"skills_{system}_after_training.json")
    return store, libraries


def _reuse_training(runner: Runner, source: Path, systems: list[str]) -> tuple[SchemaStore, dict[str, SkillLibrary]]:
    """Skip training: copy another run's training results, traces and snapshots
    into this run, so it is complete on its own, and start from its trained
    store and skill libraries. TRAINED_IN records where they came from."""
    run_dir = runner.run_dir
    training = {task.task_id for task in training_tasks()}
    results = [r for r in _load_results(source) if r.task_id in training and r.system in systems]
    copied = [f"episodes/{r.system}_{r.task_id}_L{r.layout_seed}.jsonl" for r in results]
    if "D" in systems:
        copied += [f"store_D_{label}.json" for label in ("0_prior", "1_after_first_episode", "2_after_training")]
    copied += [f"skills_{system}_after_training.json" for system in systems if system != "D"]
    for name in copied:
        (run_dir / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, run_dir / name)
    runner.keep(results)
    commit_file = source / "COMMIT"
    commit = commit_file.read_text(encoding="utf-8").strip() if commit_file.exists() else "unknown"
    (run_dir / "TRAINED_IN").write_text(f"{source.name} {commit}\n", encoding="utf-8")

    store = SchemaStore.load(run_dir / "store_D_2_after_training.json") if "D" in systems else SchemaStore()
    libraries = {
        system: SkillLibrary.load(run_dir / f"skills_{system}_after_training.json")
        for system in systems if system != "D"
    }
    _banner(f"Training reused from {source} (commit {commit}): {len(results)} episodes")
    if "D" in systems:
        print(describe_store(store))
    return store, libraries


def run_episode(args: argparse.Namespace) -> None:
    run_dir = args.run_dir or Path("runs") / time.strftime("episode-%Y%m%d-%H%M%S")
    _record_commit(run_dir)
    runner = Runner(make_backend(args.backend), LLM(mode=args.llm, cache_path=LLM_CACHE), run_dir)
    task = get_task(args.task)
    label = _next_visit_label(runner.results, args.system, task, args.layout) if args.world else None
    if args.system == "D":
        store = SchemaStore.load(args.store) if args.store else SchemaStore()
        if store.rules:
            _banner(f"Store from {args.store}")
        else:
            _banner("Store: empty. Prior rules proposed by the LLM (all hypotheses)")
            seed_prior_rules(store, runner.llm)
        print(describe_store(store))
        runner.run("D", task, args.layout, store=store, world_id=args.world, label=label)
        runner.snapshot(store, f"after_{label}" if label else "after_episode")
        _banner("Store after the episode")
        print(describe_store(store))
    else:
        library = SkillLibrary.load(args.skills) if args.skills else SkillLibrary()
        runner.run(args.system, task, args.layout, library=library, world_id=args.world, label=label)
        library.save(run_dir / f"skills_{args.system}_after_episode.json")
    print(summarize([r for r in runner.results if r.task_id == task.task_id]))


def describe_store(store: SchemaStore) -> str:
    store.certify()
    lines = []
    for rule in sorted(store.rules.values(), key=lambda r: (r.scope != TRAIN_WORLD, r.id)):
        confirmed, _ = store.replay(rule)
        episodes = len({t.episode_id for t in confirmed})
        counterexamples = rule.evidence.get("counterexamples", [])
        extra = f", {len(counterexamples)} counterexample(s) in other worlds" if counterexamples else ""
        lines.append(
            f"  {rule.status:<10} {rule.id:<34} {len(confirmed)} confirmations / {episodes} episodes"
            f"{extra}  [{rule.scope}]"
        )
    for skill in store.skills.values():
        flag = f" (flagged by {skill.flagged_by})" if skill.flagged_by else ""
        lines.append(f"  skill {skill.id:<28} {skill.status}{flag}: cites {', '.join(skill.rule_ids)}")
    return "\n".join(lines)


def make_backend(name: str) -> Backend:
    if name == "sim":
        from .environment import SimulatedWorld

        return SimulatedWorld()
    from .mineflayer_backend import MineflayerBackend

    return MineflayerBackend()


def _fake_baseline_planner(task: Task):
    """Offline stand-in for B's LLM: plan with vanilla rules, so in H3 it
    repeats the wooden-pickaxe coal mine like a skill replay would."""
    rules = fixture_prior_rules()

    def plan(inventory: dict[str, int]):
        try:
            return [step.action for step in make_plan(task.goal_item, task.goal_count, inventory, rules)]
        except PlanningError:
            return []

    return plan


def _record_commit(run_dir: Path) -> None:
    """Note which code produced the run, unless a COMMIT file is already there."""
    path = run_dir / "COMMIT"
    if path.exists():
        return
    try:
        git = partial(subprocess.run, capture_output=True, text=True, check=True)
        commit = git(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
        dirty = git(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return
    run_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(commit + ("-dirty" if dirty else "") + "\n", encoding="utf-8")


def _load_results(run_dir: Path) -> list[EpisodeResult]:
    path = run_dir / "results.json"
    if not path.exists():
        return []
    return [EpisodeResult(**raw) for raw in json.loads(path.read_text(encoding="utf-8"))]


def _next_visit_label(results: list[EpisodeResult], system: str, task: Task, seed: int) -> str:
    visits = [
        result
        for result in results
        if result.system == system and result.task_id == task.task_id and result.layout_seed == seed
    ]
    return f"visit{len(visits) + 1}"


def _seeds(text: str) -> list[int]:
    return [int(part) for part in text.split(",") if part.strip()]


def _banner(text: str) -> None:
    print(f"\n=== {text} ===")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="schema-voyager", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="train on T1-T3, then run H3 for each system")
    demo.add_argument("--backend", choices=["sim", "mc"], default="sim")
    demo.add_argument("--llm", choices=["live", "replay", "fake"], default="live")
    demo.add_argument("--systems", default="D,B", help="comma-separated: D, B, B+")
    demo.add_argument("--train-layouts", default=",".join(map(str, TRAIN_LAYOUTS)))
    demo.add_argument("--holdout-layouts", default=",".join(map(str, HOLDOUT_LAYOUTS)))
    demo.add_argument("--run-dir", type=Path)
    demo.add_argument("--from", dest="from_run", type=Path,
                      help="skip training: start H3 from this run's trained store and skill libraries")
    demo.add_argument("--second-visit", action="store_true",
                      help="after the holdouts, run D again in the first holdout layout's world")

    episode = sub.add_parser("episode", help="run one episode, e.g. for recording")
    episode.add_argument("--system", choices=["D", "B", "B+"], default="D")
    episode.add_argument("--task", default="h3")
    episode.add_argument("--layout", type=int, default=6)
    episode.add_argument("--store", type=Path, help="D: saved store to start from")
    episode.add_argument("--world", help="holdout world id to revisit")
    episode.add_argument("--skills", type=Path, help="B, B+: saved skill library to start from")
    episode.add_argument("--backend", choices=["sim", "mc"], default="mc")
    episode.add_argument("--llm", choices=["live", "replay", "fake"], default="live")
    episode.add_argument("--run-dir", type=Path)

    arena = sub.add_parser("arena", help="build a layout on the server and set its world rules")
    arena.add_argument("--layout", type=int, default=6)
    arena.add_argument("--task", default="h3")

    spectate = sub.add_parser("spectate", help="switch a player to spectator mode above the arena")
    spectate.add_argument("player")

    report = sub.add_parser("report", help="print the results table of a run")
    report.add_argument("run_dir", type=Path)

    store = sub.add_parser("store", help="print a saved schema store: rules, their evidence, and skills")
    store.add_argument("path", type=Path)

    panel = sub.add_parser("panel", help="serve the trace panel, which replays or follows episode traces")
    panel.add_argument("--port", type=int, default=8765)

    export = sub.add_parser("export", help="copy a finished run into results/ and update the field guide's numbers")
    export.add_argument("run_dir", type=Path)
    export.add_argument("--note", default="", help="shown with the results, e.g. why an episode was rerun")

    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            run_demo(args)
        elif args.command == "episode":
            run_episode(args)
        elif args.command == "arena":
            from .arena import Arena

            arena_ = Arena()
            arena_.prepare_world()
            arena_.set_world_rules(get_task(args.task).world_rules)
            arena_.build(load_layout(args.layout))
            print(f"Built layout {args.layout} with world rules {get_task(args.task).world_rules or 'vanilla'}")
        elif args.command == "spectate":
            from .arena import Arena

            print("\n".join(Arena().spectate(args.player)))
        elif args.command == "report":
            print(summarize(_load_results(args.run_dir)))
        elif args.command == "store":
            print(describe_store(SchemaStore.load(args.path)))
        elif args.command == "panel":
            from .panel import serve_panel

            serve_panel(args.port)
        elif args.command == "export":
            from .export import export_run

            export_run(args.run_dir, args.note)
    except LLMError as error:
        sys.exit(f"LLM error: {error}")


if __name__ == "__main__":
    main()
