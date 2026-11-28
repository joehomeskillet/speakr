"""Run the synthetic clinic persona panel with TinyTroupe (MIT)."""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
STATE_DIR = Path.home() / ".local/share/tinytroupe"
PERSONAS = HERE / "personas"
DEFAULT_CAP = 400_000


class CapExceeded(BaseException):
    """Stop the run even when TinyTroupe catches and retries normal exceptions."""


def proxy_key(env_file):
    with env_file.open(encoding="utf-8") as stream:
        for line in stream:
            name, _, value = line.strip().removeprefix("export ").partition("=")
            if name.strip() == "PROXY_API_KEY":
                return value.strip().strip("\"'")
    return ""


def persona_brief(mode, subject, context):
    if mode == "walkthrough":
        return (
            f"You are testing a web app for dictation in a clinic (flow: {subject}). "
            "The screenshots show screens of the flow in order. Use only what you can see. "
            f"{context}\nTask: act as in your real daily work. Answer in German, concretely, "
            "in your own voice, in ONE message (TALK once, then DONE) with these sections:\n"
            "1. WALKTHROUGH: step by step what you would do on each screen (screen 1, 2, ...).\n"
            "2. CONFUSION: what is unclear, hesitating, or misleading (cite what you see on the screen).\n"
            "3. UNKNOWN WORDS: every word or label on the screens you did not understand (quote it exactly), per screen.\n"
            "4. UNSURE MOMENT: the exact moment you would not know what to do next (which screen, what you see).\n"
            "5. FEARS: anything you feared might delete, lose or send something (or send it to the wrong person).\n"
            "6. BLOCKERS: what would make you stop using it.\n"
            "7. PROPOSALS: up to 3 concrete UI changes you want.\n"
            "8. LAIEN: per screen, 'Screen N: X/10' (1 = I do not understand it at all, "
            "10 = obvious even for a non-technical person), judged from your own tech literacy.\n"
            "9. SCORE: 1-10 how likely you would use this daily, one sentence why."
        )
    return (
        "Focus group about a web app for dictation in a clinic (dictate, upload, assign to typists, correct, deliver). "
        f"{context}\nTopic: {subject}\nAnswer in German, in your own voice, in ONE message (TALK once, then DONE): "
        "up to 4 concrete ideas or requirements for this topic from YOUR role's perspective, each with a one-line reason "
        "and a priority (must/should/nice), then up to 2 risks or objections."
    )


def synthesise(client, mode, subject, results):
    payload = "\n\n".join(f"### {item['name']} ({pid})\n{item['text']}" for pid, item in results.items())
    if mode == "walkthrough":
        task = (
            "Rank confusion points and blockers by severity x number of personas affected. "
            "Give affected personas and a concrete UI change for each. Include '## Laienverstaendlichkeit' "
            "(one row per screen with average score X/10, lowest-scoring persona and words nobody understood), "
            "'Unknown words' with counts and plain-language replacements, 'Fears' and 'Unsure moments'."
        )
    else:
        task = "Cluster and rank the ideas by value x number of personas supporting. Note conflicts between roles."
    response = client.send_message([
        {"role": "system", "content": "Synthesise a synthetic persona panel for a product owner. "
         "Be concrete and short. English prose; keep German quotes where useful. "
         "Never invent facts beyond the persona statements."},
        {"role": "user", "content": f"Flow/topic: {subject}\n{task}\n"
         "Use markdown with '## Top findings' (max 10 numbered items, with severity, personas, evidence and proposed change), "
         "'## Per-role highlights' (one line per persona) and '## Disagreements'. Weigh findings from personas with "
         f"limited technical experience accordingly. Never invent scores.\n\n{payload}"},
    ], max_completion_tokens=6000, temperature=0.3)
    return response.get("content", "") if response else ""


def main():
    parser = argparse.ArgumentParser(prog="persona-panel", description=__doc__)
    parser.add_argument("mode", choices=["brainstorm", "walkthrough", "list"])
    parser.add_argument("subject", nargs="?", help="topic (brainstorm) or flow name (walkthrough)")
    parser.add_argument("images", nargs="*", help="walkthrough: staging screenshots")
    parser.add_argument("--personas", help="comma-separated persona IDs (default: all)")
    parser.add_argument(
        "--cap-tokens", type=int, default=DEFAULT_CAP,
        help="stop once reported usage reaches this threshold (default: %(default)s)",
    )
    parser.add_argument("--context", default="", help="extra product context, text or @file (synthetic only)")
    args = parser.parse_args()

    available = {p.name.removesuffix(".agent.json"): p for p in sorted(PERSONAS.glob("*.agent.json"))}
    if args.mode == "list":
        print("\n".join(available))
        return 0
    if not args.subject:
        parser.error("subject required")
    if args.cap_tokens <= 0:
        parser.error("--cap-tokens must be positive")
    ids = args.personas.split(",") if args.personas else list(available)
    for pid in ids:
        if pid not in available:
            parser.error(f"unknown persona {pid}; have {', '.join(available)}")
    images = [Path(image).expanduser().resolve() for image in args.images]
    if args.mode == "walkthrough":
        if not images:
            parser.error("walkthrough needs at least one screenshot")
        for image in images:
            if not image.is_file():
                parser.error(f"screenshot not found: {image}")

    python = Path(os.environ.get("PERSONA_PANEL_PYTHON", STATE_DIR / "venv/bin/python")).expanduser().absolute()
    if python != Path(sys.executable).absolute():
        try:
            os.execv(python, [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
        except OSError:
            parser.error("cannot execute PERSONA_PANEL_PYTHON")

    base_url = os.environ.get("PERSONA_PANEL_BASE_URL", "http://127.0.0.1:8317/v1")
    try:
        url = urlsplit(base_url)
        valid_url = url.scheme in {"https", "http"} and url.hostname and url.username is None
    except ValueError:
        valid_url = False
    if not valid_url or url.query or url.fragment:
        parser.error("PERSONA_PANEL_BASE_URL must be an HTTP(S) URL without credentials, query or fragment")
    model = os.environ.get("PERSONA_PANEL_MODEL", "gpt-6.2-sol")
    env_file = Path(os.environ.get("PERSONA_PANEL_ENV_FILE", STATE_DIR / "cliproxy.env")).expanduser()
    runs = Path(os.environ.get("PERSONA_PANEL_RUNS", STATE_DIR / "runs")).expanduser().resolve()
    if runs.is_relative_to(HERE.parents[1]):
        parser.error("PERSONA_PANEL_RUNS must be outside the checkout")

    context = args.context
    if context.startswith("@"):
        try:
            context = Path(context[1:]).expanduser().read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            parser.error("cannot read --context file")
    try:
        key = proxy_key(env_file)
    except (OSError, UnicodeError):
        parser.error("cannot read PERSONA_PANEL_ENV_FILE")
    if not key:
        parser.error("PERSONA_PANEL_ENV_FILE must define PROXY_API_KEY")
    os.environ["OPENAI_BASE_URL"] = base_url
    os.environ["OPENAI_API_KEY"] = key

    slug = re.sub(r"[^a-z0-9]+", "-", args.subject.lower()).strip("-")[:40]
    prefix = f"{time.strftime('%Y%m%d-%H%M%S')}-{args.mode}-{slug}-"
    runs.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix=prefix, dir=runs))
    # TinyTroupe reads ./config.ini on import and uses the working directory for its cache.
    shutil.copyfile(HERE / "config.ini.example", out / "config.ini")
    os.chdir(out)
    logging.disable(logging.CRITICAL)

    from tinytroupe import clients, config_manager
    from tinytroupe.agent import TinyPerson

    for setting in ("model", "reasoning_model", "vision_model"):
        config_manager.update(setting, model)
    client = clients.client()
    raw_model_call = client._raw_model_call

    def check_cap():
        if client.get_cost_stats()["total_tokens"] >= args.cap_tokens:
            raise CapExceeded(f"token cap {args.cap_tokens} reached")

    def capped(model_name, params):
        check_cap()
        return raw_model_call(model_name, params)

    client._raw_model_call = capped
    brief = persona_brief(args.mode, args.subject, context)
    started = time.monotonic()
    results = {}
    synthesis = ""
    aborted = None
    try:
        for pid in ids:
            try:
                persona = TinyPerson.load_specification(str(available[pid]))
                if args.mode == "walkthrough":
                    persona.see(images=[str(image) for image in images], description=brief)
                else:
                    persona.listen(brief)
                texts = []
                for item in persona.act(return_actions=True) or []:
                    action = item.get("action", item) if isinstance(item, dict) else {}
                    if action.get("type") == "TALK" and action.get("content"):
                        texts.append(action["content"])
                results[pid] = {"name": persona.name, "text": "\n\n".join(texts)}
                print(f"[{pid}] ok {len(results[pid]['text'])} chars", file=sys.stderr)
            except Exception as error:
                # Proxy error messages can contain authentication material.
                kind = type(error).__name__
                results[pid] = {"name": pid, "text": "", "error": kind}
                print(f"[{pid}] FAILED {kind}", file=sys.stderr)
            check_cap()

        completed = {pid: item for pid, item in results.items() if item["text"]}
        if completed:
            synthesis = synthesise(client, args.mode, args.subject, completed)
            check_cap()
    except CapExceeded as error:
        aborted = str(error)

    stats = client.get_cost_stats()
    lines = [
        f"# Persona panel: {args.mode} - {args.subject}", "",
        "> Synthetic personas and synthetic staging data only. Suggestions for the owner, not decisions.", "",
        f"- model: {model} via {base_url}", f"- personas: {', '.join(ids)}",
    ]
    if args.mode == "walkthrough":
        lines.append("- screenshots: " + ", ".join(f"`{image}`" for image in args.images))
    lines.append(
        f"- usage: {stats['model_calls']} calls ({stats['cached_calls']} cached), "
        f"{stats['input_tokens']} in / {stats['output_tokens']} out / {stats['total_tokens']} total tokens "
        f"(cap {args.cap_tokens}), {time.monotonic() - started:.0f}s"
    )
    if aborted:
        lines.append(f"- ABORTED: {aborted}")
    failed = [f"{pid} ({item['error']})" for pid, item in results.items() if "error" in item]
    if failed:
        lines.append("- failed personas: " + ", ".join(failed))
    lines.extend(["", synthesis or "_No synthesis available._", "", "---", "## Raw persona output", ""])
    for pid, item in results.items():
        lines.extend([f"### {item['name']} ({pid})", "", item["text"] or f"_no output: {item.get('error', 'empty')}_", ""])
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    raw = {"results": results, "stats": stats, "subject": args.subject}
    (out / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out / "report.md")
    return 0 if synthesis and not aborted else 2


if __name__ == "__main__":
    sys.exit(main())
