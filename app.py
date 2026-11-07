"""Terminal workflow: create or use a GitHub repo, then generate content, commit locally, and push once."""

from __future__ import annotations

import textwrap
import difflib
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from html import escape
from pathlib import Path

os.system("")  # turns on ANSI codes in Windows terminals so colors and redraws work

LOG_FILE = Path(__file__).resolve().parent / "commit-cycle.log"
DELAY_SECONDS = 3
COMMIT_PREFIX = "chore(auto):"
MAIN_BRANCH = "main"
SESSION_PREFIX = "auto/session-"
GENERATED_DIR = "generated"

AUTO_START = "<!-- auto:start -->"
AUTO_END = "<!-- auto:end -->"
AUTO_BLOCK = re.compile(re.escape(AUTO_START) + r"\n.*?" + re.escape(AUTO_END) + r"\n?", re.DOTALL)
MAX_GENERATED_BLOCKS = 8        # generated blocks kept in index.html / README.md; older ones are removed
LEGACY_MAX_BYTES = 4000         # old unmarked files larger than this are reset once
RESET_UNMARKED_OVERSIZE = True  # set to False if you wrote your own content into these files

# ---------- Terminal UI settings ----------

WIDTH = 64
USE_COLOR = sys.stdout.isatty()
ANSI_CODE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STYLES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "cyan": "36"}
WARNING_TITLE = "IMPORTANT: READ THIS BEFORE CONTINUING"
WARNING_CONFIRM_PHRASE = "I UNDERSTAND AND ACCEPT THIS RISK"
WARNING_PARAGRAPHS = [
    "This tool creates commits that are not real work. It makes small generated edits to files such as index.html, README.md, and the generated/ folder, then pushes them to your GitHub account under your name.",
    "Anyone who looks at your profile, contribution graph, or repositories may assume these commits show your actual progress. They do not. Recruiters, collaborators, clients, and friends can be misled by that, and it can affect how people trust you.",
    "GitHub's Terms of Service and Acceptable Use Policies prohibit inauthentic or manipulative activity. Accounts that do this can be restricted or suspended, and the damage to your reputation can last longer than the commits themselves.",
    "If you wanted a real commit, stop here. Write real code, commit it yourself, and do not use this script.",
    "If you continue, you accept full responsibility for every commit this tool creates, for what other people assume from them, and for anything GitHub does to your account as a result.",
]


def paint(text: str, *styles: str) -> str:
    if not USE_COLOR or not styles:
        return text
    return f"\033[{';'.join(STYLES[s] for s in styles)}m{text}\033[0m"


def visible_len(text: str) -> int:
    return len(ANSI_CODE.sub("", text))


def ui_header(title: str, subtitle: str = "") -> None:
    print()
    print(paint(f" {title}", "bold", "cyan"))
    if subtitle:
        print(paint(f" {subtitle}", "dim"))
    print(paint("─" * WIDTH, "dim"))


def ui_panel(title: str, lines: list[str], style: str = "cyan") -> None:
    inner = WIDTH - 4
    top_fill = "─" * max(0, WIDTH - visible_len(title) - 5)
    print(paint(f"┌─ {title} {top_fill}┐", style))
    for line in lines:
        padding = " " * max(0, inner - visible_len(line))
        print(paint("│ ", style) + line + padding + paint(" │", style))
    print(paint("└" + "─" * (WIDTH - 2) + "┘", style))


def ui_step(number: int, total: int, text: str) -> None:
    print()
    print(f"{paint(f'[{number}/{total}]', 'bold', 'blue')} {paint(text, 'bold')}")


def ui_info(text: str) -> None:
    print(f"  {paint('•', 'cyan')} {text}")


def ui_ok(text: str) -> None:
    print(f"  {paint('✔', 'green')} {text}")


def ui_warn(text: str) -> None:
    print(f"  {paint('!', 'yellow')} {text}")


def ui_fail(text: str) -> None:
    print(f"  {paint('✘', 'red')} {text}")


def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    return input(f"  {paint('›', 'cyan')} {prompt}{hint}: ").strip() or default


def progress_bar(done: int, total: int, width: int = 24) -> str:
    done = max(0, min(done, total))
    filled = round(width * done / total) if total else 0
    percent = round(100 * done / total) if total else 0
    bar = paint("=" * filled, "green") + "-" * (width - filled)
    return f"[{bar}] {done}/{total} ({percent}%)"


def pause(reason: str) -> None:
    print(paint(f"    … waiting {DELAY_SECONDS}s ({reason})", "dim"))
    time.sleep(DELAY_SECONDS)


# ---------- Basic command and logging helpers ----------

def command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True)


def log(text: str) -> None:
    """Append text to commit-cycle.log with a timestamp on every line."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        for line in text.splitlines() or [""]:
            handle.write(f"[{stamp}] {line}\n")


# ---------- Spinner and progress redraw ----------

def redraw(lines: list[str], previous: int) -> int:
    """Rewrite the previously drawn lines in place. Returns how many lines are now on screen."""
    if previous:
        sys.stdout.write(f"\033[{previous}A")  # move the cursor back to the first drawn line
    for line in lines:
        sys.stdout.write(f"\r\033[2K{line}\n")
    sys.stdout.flush()
    return len(lines)


def clear_lines(count: int) -> None:
    """Erase the drawn lines and leave the cursor where they started."""
    if not count:
        return
    sys.stdout.write(f"\033[{count}A")
    for _ in range(count):
        sys.stdout.write("\r\033[2K\n")
    sys.stdout.write(f"\033[{count}A")
    sys.stdout.flush()


def command_with_spinner(
    label: str,
    args: list[str],
    cwd: Path | None = None,
    progress: tuple[int, int] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a slow command with a spinner, plus an optional progress bar on the line below."""
    process = subprocess.Popen(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result: list[subprocess.CompletedProcess[str]] = []

    def wait() -> None:
        stdout, stderr = process.communicate()
        result.append(subprocess.CompletedProcess(args, process.returncode, stdout, stderr))

    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    frames = "|/-\\"
    index = 0
    drawn = 0
    while thread.is_alive():
        lines = [f"[{frames[index % len(frames)]}] {label}"]
        if progress:
            lines.append("  " + progress_bar(*progress))
        drawn = redraw(lines, drawn)
        index += 1
        time.sleep(0.12)
    thread.join()
    clear_lines(drawn)
    return result[0]


# ---------- Setup: tools, sign-in, identity ----------

def refresh_path() -> None:
    """Add common Windows install locations for tools installed during this run."""
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs",
        Path.home() / "scoop" / "apps",
    ]
    candidates = [
        roots[0] / "Git" / "cmd",
        roots[0] / "GitHub CLI",
        roots[1] / "GitHub CLI",
        roots[2] / "gh" / "current" / "bin",
    ]
    for directory in candidates:
        if directory.exists() and str(directory) not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")


def ensure_tools() -> None:
    refresh_path()
    missing = [tool for tool in ("git", "gh") if not shutil.which(tool)]
    if not missing:
        return
    if not shutil.which("winget"):
        sys.exit("Git/GitHub CLI is missing and winget is unavailable. Install App Installer, then rerun this program.")
    for tool, package in (("git", "Git.Git"), ("gh", "GitHub.cli")):
        if shutil.which(tool):
            continue
        ui_info(f"Installing {tool} with winget…")
        result = subprocess.run([
            "winget", "install", "--id", package, "--exact", "--silent",
            "--accept-source-agreements", "--accept-package-agreements",
        ])
        if result.returncode and not shutil.which(tool):
            sys.exit(f"Could not install {tool}. Rerun this program after resolving the winget error.")
    # Newly installed command line tools are not always on this process's PATH yet.
    refresh_path()
    still_missing = [tool for tool in ("git", "gh") if not shutil.which(tool)]
    if still_missing:
        sys.exit("Installed tool(s) not found on PATH: " + ", ".join(still_missing) + ". Close and reopen the terminal, then rerun this program.")


def sign_in() -> None:
    if command(["gh", "auth", "status"]).returncode == 0:
        ui_ok("GitHub account is signed in")
        return
    ui_info("GitHub sign-in is required. Follow the prompts below.")
    ui_info("If no browser opens, copy the one-time code shown here and visit the URL printed by GitHub.")
    print()
    # Inherit the terminal so gh's device code and interactive prompts are visible.
    result = subprocess.run(["gh", "auth", "login", "--web"])
    if result.returncode or command(["gh", "auth", "status"]).returncode:
        sys.exit("GitHub sign-in did not complete. Run `gh auth login --web` and try again.")
    ui_ok("GitHub account is signed in")


def ensure_identity() -> None:
    name = command(["git", "config", "--global", "user.name"]).stdout.strip()
    email = command(["git", "config", "--global", "user.email"]).stdout.strip()
    if not name:
        name = ask("Git commit name")
        if name:
            subprocess.run(["git", "config", "--global", "user.name", name], check=True)
    if not email:
        email = ask("Git commit email")
        if email:
            subprocess.run(["git", "config", "--global", "user.email", email], check=True)
    if not name or not email:
        sys.exit("A Git name and email are required to create commits.")


# ---------- Repository selection ----------

def create_repository() -> Path | None:
    name = ask("New GitHub repository name")
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in name):
        sys.exit("Use letters, numbers, dots, underscores, or hyphens in the repository name.")
    default_parent = Path.home() / "Projects"
    raw_parent = ask("Parent folder (the project folder is <parent>\\name)", str(default_parent))
    parent = Path(raw_parent).expanduser()
    repo = parent / name
    # If the user selected a folder already named after the project, use that folder
    # directly rather than accidentally creating name\name inside it.
    if parent.name.casefold() == name.casefold() and parent.is_dir() and not repo.exists():
        repo = parent
    if repo.exists():
        ui_warn(f"The destination folder already exists: {repo}")
        ui_warn("Deleting it will permanently remove ALL files inside it.")
        print("    1) Use it as an existing repository")
        print("    2) Delete it and create a fresh folder")
        print("    3) Return to the main selection")
        collision = ask("Choose 1, 2, or 3")
        if collision == "1":
            return validate_existing(repo)
        if collision == "2":
            confirmation = ask(f'Type DELETE to permanently remove "{repo}"')
            if confirmation != "DELETE":
                ui_info("Deletion cancelled; returning to the main selection.")
                return None
            shutil.rmtree(repo)
        else:
            return None
    visibility = ask("Visibility: [P]rivate or p[U]blic", "private").lower()
    visibility_flag = "--public" if visibility in ("u", "public") else "--private"

    parent.mkdir(parents=True, exist_ok=True)
    repo.mkdir()
    result = command(["git", "init", "-b", MAIN_BRANCH], cwd=repo)
    if result.returncode:
        sys.exit(result.stderr or result.stdout)
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)
    result = command_with_spinner(
        "Creating GitHub repository and pushing initial commit",
        ["gh", "repo", "create", name, visibility_flag, "--source", str(repo), "--remote", "origin", "--push"],
        cwd=repo,
    )
    if result.returncode:
        ui_fail("GitHub setup or initial push did not finish successfully.")
        print(result.stderr or result.stdout or "GitHub repository creation failed.")
        ui_info(f"Your local project is still here: {repo}")
        if "Name already exists" in (result.stderr + result.stdout):
            ui_warn("That repository name already exists on your GitHub account.")
            print("    1) Link this local project to that GitHub repository and try pushing")
            print("    2) Choose a different repository name")
            print("    3) Return to the main selection")
            recovery = ask("Choose 1, 2, or 3")
            if recovery == "1":
                remote = command(["gh", "repo", "view", name, "--json", "url", "--jq", ".url"])
                if remote.returncode:
                    print(remote.stderr or "Could not find that GitHub repository.")
                    return None
                origin = command(["git", "remote", "get-url", "origin"], cwd=repo)
                if origin.returncode:
                    added = command(["git", "remote", "add", "origin", remote.stdout.strip()], cwd=repo)
                    if added.returncode:
                        print(added.stderr or "Could not configure the origin remote.")
                        return None
                pushed = command_with_spinner("Pushing to the existing GitHub repository", ["git", "push", "-u", "origin", "HEAD"], cwd=repo)
                if pushed.returncode:
                    print(pushed.stderr or pushed.stdout or "Push failed.")
                    ui_info("The local project is still available; choose the existing repository option to retry later.")
                    return None
                ui_ok("Linked to the existing GitHub repository and pushed successfully")
                return repo
            return None
        ui_info("You can return to the main selection and retry with the existing repository option.")
        return None
    ui_ok(f"Repository created and initial commit pushed: {repo}")
    return repo


def validate_existing(repo: Path) -> Path | None:
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        ui_fail(f"Folder not found: {repo}")
        return None
    result = command(["git", "rev-parse", "--show-toplevel"], cwd=repo)
    if result.returncode:
        ui_fail(f"That folder is not a Git repository: {repo}")
        return None
    repo = Path(result.stdout.strip()).resolve()
    remote = command(["git", "remote", "get-url", "origin"], cwd=repo)
    if remote.returncode:
        ui_warn("This repository has no 'origin' remote configured yet.")
        guess = repo.name
        remote_name = ask("GitHub repository to connect (owner/name or URL)", guess)
        if remote_name.startswith(("https://", "git@")):
            remote_url = remote_name
        else:
            found = command(["gh", "repo", "view", remote_name, "--json", "url", "--jq", ".url"])
            if found.returncode:
                ui_fail(found.stderr.strip() or f"Could not find GitHub repository '{remote_name}'.")
                return None
            remote_url = found.stdout.strip()
        added = command(["git", "remote", "add", "origin", remote_url], cwd=repo)
        if added.returncode:
            ui_fail(added.stderr.strip() or "Could not configure the origin remote.")
            return None
        remote = command(["git", "remote", "get-url", "origin"], cwd=repo)
    ui_ok(f"Using repository: {repo}")
    ui_info(f"Push remote: {remote.stdout.strip()}")
    return repo


def choose_existing_repository() -> Path | None:
    raw = ask("Path to the existing local Git repository").strip('"')
    return validate_existing(Path(raw))


# ---------- Git helpers ----------

def git_changes(repo: Path, target: str | None = None) -> list[str] | None:
    """Return porcelain status lines (optionally for one file), or None if Git failed."""
    args = ["git", "status", "--porcelain", "--untracked-files=all"]
    if target:
        args += ["--", target]
    result = command(args, cwd=repo)
    if result.returncode:
        ui_fail(result.stderr.strip() or "Could not read Git status.")
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def session_branches(repo: Path) -> list[str]:
    """Local session branches left behind by an unfinished run."""
    result = command(["git", "branch", "--list", f"{SESSION_PREFIX}*", "--format=%(refname:short)"], cwd=repo)
    return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())


def main_is_ahead(repo: Path) -> bool:
    """True if local main has commits that were never pushed."""
    status = command(["git", "status", "-sb"], cwd=repo)
    first_line = status.stdout.splitlines()[0] if status.stdout else ""
    return "ahead" in first_line


def stash_leftovers(repo: Path) -> bool:
    """Save an unfinished generated edit to the stash so branches can be switched safely."""
    leftovers = git_changes(repo)
    if leftovers is None:
        return False
    if leftovers:
        stashed = command(["git", "stash", "push", "--include-untracked", "-m", "commit-cycle leftover"], cwd=repo)
        if stashed.returncode:
            ui_fail(stashed.stderr.strip() or "Could not save unfinished changes.")
            return False
        ui_warn("Saved an unfinished generated edit to git stash (message: commit-cycle leftover).")
        log("Stashed unfinished generated edit: " + ", ".join(leftovers))
    return True


def push_with_retry(repo: Path, label: str, attempts: int = 5, progress: tuple[int, int] | None = None) -> bool:
    for attempt in range(1, attempts + 1):
        pushed = command_with_spinner(
            f"{label} (try {attempt}/{attempts})",
            ["git", "push", "-u", "origin", "HEAD"],
            cwd=repo,
            progress=progress,
        )
        output = (pushed.stdout + pushed.stderr).strip()
        log(f"git push (try {attempt}/{attempts}) exit={pushed.returncode}\n{output}")
        if pushed.returncode == 0:
            ui_ok("Pushed to GitHub")
            return True
        ui_fail(f"Push try {attempt} failed")
        for line in output.splitlines()[:4]:
            print(paint(f"    {line}", "dim"))
        if attempt < attempts:
            ui_warn("Retrying in 10 seconds…")
            time.sleep(10)
    return False


def finish_session(repo: Path, branch: str) -> bool:
    """Squash the local session branch into one commit on main, push main once, then delete the local branch."""
    ui_info(f"Finishing {branch}")
    if not stash_leftovers(repo):
        return False
    counted = command(["git", "rev-list", "--count", f"{MAIN_BRANCH}..{branch}"], cwd=repo)
    count = int(counted.stdout.strip() or 0) if counted.returncode == 0 else 0
    if command(["git", "checkout", MAIN_BRANCH], cwd=repo).returncode:
        ui_fail(f"Could not switch to {MAIN_BRANCH}. Fix the Git state, then run the app again.")
        return False

    if count:
        merged = command(["git", "merge", "--squash", branch], cwd=repo)
        if merged.returncode:
            ui_fail("Merge conflict: main and the session changed the same lines.")
            print(merged.stdout or merged.stderr)
            command(["git", "reset", "--merge"], cwd=repo)
            log(f"Squash merge of {branch} failed: {merged.stdout or merged.stderr}")
            ui_warn(f"{MAIN_BRANCH} was left unchanged. Resolve the conflict manually, then run the app again.")
            return False
        # A non-zero exit here means there are staged changes to commit.
        if command(["git", "diff", "--cached", "--quiet"], cwd=repo).returncode:
            committed = command(["git", "commit", "-m", f"{COMMIT_PREFIX} merged {count} generated changes"], cwd=repo)
            if committed.returncode:
                ui_fail("Squash commit failed.")
                print(committed.stderr or committed.stdout)
                log(f"Squash commit failed: {committed.stderr or committed.stdout}")
                return False
            ui_ok(f"Squashed {count} commits into one commit on {MAIN_BRANCH}")
            log(committed.stdout.strip())

    if main_is_ahead(repo) and not push_with_retry(repo, f"Pushing {MAIN_BRANCH}"):
        ui_warn(f"Squashed commit is on {MAIN_BRANCH} locally but not pushed. Run the app again to push it.")
        return False

    command(["git", "branch", "-D", branch], cwd=repo)
    log(f"Session {branch} merged into {MAIN_BRANCH} ({count} commits squashed)")
    return True


# ---------- Pure-Python content generator (no AI, no API) ----------

ADJECTIVES = ["simple", "fast", "clean", "reliable", "friendly", "focused", "practical", "modern", "careful", "bright"]
NOUNS = ["workflow", "routine", "toolkit", "checklist", "process", "setup", "system", "plan"]
VERBS = ["helps you", "makes it easy to", "lets you", "gives you a way to", "keeps it simple to"]
ACTIONS = ["build", "review", "share", "update", "organize", "track", "improve", "explain"]
OBJECTS = ["your ideas", "the next step", "small changes", "daily progress", "the main details", "new features"]
HEADINGS = ["Why it matters", "Key highlights", "Getting started", "Behind the scenes", "Quick tips",
            "Good to know", "Next steps", "What people ask", "Under the hood", "Roadmap"]
CARD_TITLES = ["Plan", "Build", "Check", "Share", "Grow", "Learn"]
FAQ_QUESTIONS = ["How do I start?", "Can I change it later?", "Who is it for?", "How often is it updated?"]
STAT_LABELS = ["pages", "updates", "ideas", "notes", "steps"]
CHECKLIST_ITEMS = ["Re-read the goal", "Pick one thing to improve", "Test it once", "Write a short note", "Share the result"]

COUNTER_JS = """  <script id="counter-TOKEN">
    (() => {
      let count = 0;
      const button = document.createElement("button");
      button.textContent = "Clicked 0 times";
      button.addEventListener("click", () => {
        count += 1;
        button.textContent = `Clicked ${count} times`;
      });
      document.querySelector("main").appendChild(button);
    })();
  </script>
"""

CLOCK_JS = """  <script id="clock-TOKEN">
    (() => {
      const out = document.createElement("p");
      document.querySelector("main").appendChild(out);
      const tick = () => { out.textContent = "Local time: " + new Date().toLocaleTimeString(); };
      tick();
      setInterval(tick, 1000);
    })();
  </script>
"""


def pick_target(cycle: int) -> str:
    """Rotate: index.html, README.md, then a new .py, .css, and .js file (repeats)."""
    slot = (cycle - 1) % 5
    if slot == 0:
        return "index.html"
    if slot == 1:
        return "README.md"
    if slot == 2:
        return f"{GENERATED_DIR}/module_{cycle}.py"
    if slot == 3:
        return f"{GENERATED_DIR}/style_{cycle}.css"
    return f"{GENERATED_DIR}/script_{cycle}.js"


def wrap_generated(block: str) -> str:
    return f"{AUTO_START}\n{block.rstrip()}\n{AUTO_END}\n"


def prune_generated(text: str, keep: int = MAX_GENERATED_BLOCKS) -> str:
    """Remove the oldest generated blocks so only the newest `keep` remain."""
    matches = list(AUTO_BLOCK.finditer(text))
    if len(matches) <= keep:
        return text
    pieces = []
    last = 0
    for match in matches[: len(matches) - keep]:
        pieces.append(text[last:match.start()])
        last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def fresh_file(filename: str, subject: str) -> str:
    if filename.lower().endswith(".md"):
        return f"# {subject}\n"
    return _skeleton(subject)


def _sentence(rng: random.Random, subject: str) -> str:
    return (f"{subject} {rng.choice(VERBS)} {rng.choice(ACTIONS)} {rng.choice(OBJECTS)} "
            f"with a {rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}.")


def _paragraph(rng: random.Random, subject: str, sentences: int = 2) -> str:
    return " ".join(_sentence(rng, subject) for _ in range(sentences))


def _skeleton(subject: str) -> str:
    title = escape(subject)
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{title}</title>\n</head>\n<body>\n  <main>\n"
        f"    <h1>{title}</h1>\n  </main>\n</body>\n</html>\n"
    )


def _insert_before(text: str, marker: str, block: str) -> str:
    """Insert a marked block on its own line just before the last occurrence of marker."""
    block = wrap_generated(block)
    index = text.rfind(marker)
    if index == -1:
        return text + block
    line_start = text.rfind("\n", 0, index) + 1
    if text[line_start:index].strip() == "":
        index = line_start  # marker is the first thing on its line; insert above that line
    return text[:index] + block + text[index:]


def _html_section(rng: random.Random, subject: str, token: int) -> str:
    kind = rng.choice(["text", "cards", "faq", "stats", "steps"])
    heading = rng.choice(HEADINGS)
    if kind == "cards":
        cards = "".join(
            f'      <article class="card">\n        <h3>{title}</h3>\n'
            f'        <p>{escape(_paragraph(rng, subject, 1))}</p>\n      </article>\n'
            for title in rng.sample(CARD_TITLES, 3)
        )
        inner = f'    <div class="cards">\n{cards}    </div>\n'
    elif kind == "faq":
        inner = "".join(
            f"    <details>\n      <summary>{question}</summary>\n"
            f"      <p>{escape(_paragraph(rng, subject, 1))}</p>\n    </details>\n"
            for question in rng.sample(FAQ_QUESTIONS, 2)
        )
    elif kind == "stats":
        items = "".join(
            f"      <li><strong>{rng.randint(10, 999)}</strong> {label}</li>\n"
            for label in rng.sample(STAT_LABELS, 3)
        )
        inner = f'    <ul class="stats">\n{items}    </ul>\n'
    elif kind == "steps":
        items = "".join(
            f"      <li>{rng.choice(ACTIONS).capitalize()} {rng.choice(OBJECTS)}</li>\n" for _ in range(3)
        )
        inner = f"    <ol>\n{items}    </ol>\n"
    else:
        inner = f"    <p>{escape(_paragraph(rng, subject))}</p>\n"
    return f'  <section id="{kind}-{token}">\n    <h2>{heading}</h2>\n{inner}  </section>\n'


def _html_style(rng: random.Random, token: int) -> str:
    hue = rng.randrange(360)
    return (
        f'  <style id="theme-{token}">\n'
        f"    :root {{ --accent: hsl({hue} 70% 45%); }}\n"
        "    main h2 { color: var(--accent); }\n"
        "  </style>\n"
    )


def _generate_html(subject: str, current: str, rng: random.Random, token: int) -> str:
    kind = rng.choice(["section", "section", "section", "script", "style"])
    if kind == "section":
        marker = "</main>" if "</main>" in current else "</body>"
        return _insert_before(current, marker, _html_section(rng, subject, token))
    if kind == "script":
        script = rng.choice([COUNTER_JS, CLOCK_JS]).replace("TOKEN", str(token))
        return _insert_before(current, "</body>", script)
    return _insert_before(current, "</head>", _html_style(rng, token))


def _python_helper(rng: random.Random, token: int) -> str:
    op = rng.choice(["+", "*", "-"])
    number = rng.randint(2, 9)
    return (
        f"def helper_{token}(values):\n"
        f'    """Generated helper {token}."""\n'
        f"    return [value {op} {number} for value in values]\n"
    )


def _generate_markdown(subject: str, rng: random.Random, token: int) -> str:
    kind = rng.choice(["text", "checklist", "table", "code"])
    heading = rng.choice(HEADINGS)
    if kind == "checklist":
        body = "\n".join(f"- [ ] {item}" for item in rng.sample(CHECKLIST_ITEMS, 3))
    elif kind == "table":
        rows = "\n".join(f"| {label} | {rng.randint(1, 99)} |" for label in rng.sample(STAT_LABELS, 3))
        body = "| Item | Count |\n|---|---|\n" + rows
    elif kind == "code":
        body = "```python\n" + _python_helper(rng, token) + "```"
    else:
        body = _paragraph(rng, subject)
    return f"\n## {heading}\n\n{body}\n"


def _generate_code(filename: str, subject: str, rng: random.Random, token: int) -> str:
    lower = filename.lower()
    number = rng.randint(2, 9)
    if lower.endswith(".py"):
        return _python_helper(rng, token)
    if lower.endswith((".js", ".mjs")):
        return f"export function helper{token}(values) {{\n  return values.map((value) => value * {number});\n}}\n"
    if lower.endswith(".css"):
        return f".auto-{token} {{\n  margin-top: {number}px;\n}}\n"
    return _paragraph(rng, subject) + "\n"


def generate_content(goal: str, filename: str, current: str, cycle: int) -> str:
    """Build new content for the file in pure Python. Each call produces different output."""
    rng = random.Random(f"{goal}|{cycle}|{time.time_ns()}")
    token = rng.randrange(100000, 1000000)
    subject = goal[:1].upper() + goal[1:]
    lower = filename.lower()
    if lower.endswith((".html", ".htm")):
        base = current if current.strip() else _skeleton(subject)
        return prune_generated(_generate_html(subject, base, rng, token))
    if lower.endswith(".md"):
        block = wrap_generated(_generate_markdown(subject, rng, token))
        return prune_generated(current.rstrip() + "\n\n" + block).lstrip("\n")
    block = _generate_code(filename, subject, rng, token)
    return (current.rstrip() + "\n\n" + block.strip("\n") + "\n").lstrip("\n")


def show_diff(old: str, new: str) -> list[str]:
    """Only the added/removed lines, without context, so the log stays readable."""
    return [
        line for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
        if not line.startswith(("---", "+++", "@@"))
    ]


# ---------- Generation loop ----------

def generate_loop(repo: Path) -> None:
    subject = repo.name.replace("-", " ").replace("_", " ").strip().title() or "Project"
    try:
        limit = int(ask("How many commits? (1–200)", "5"))
    except ValueError:
        ui_fail("Enter a whole number from 1 to 200.")
        return
    if not 1 <= limit <= 200:
        ui_fail("The commit count must be from 1 to 200.")
        return

    ui_panel("Session", [
        f"Repo      {repo.name}",
        f"Topic     {subject}",
        f"Commits   {limit}",
        "Rotation  index.html > README.md > .py > .css > .js",
        f"Delay     {DELAY_SECONDS}s after each edit, commit, and push",
        f"Mode      one commit per change, directly on {MAIN_BRANCH}",
    ])

    before = git_changes(repo)
    if before is None:
        return
    if before:
        ui_fail("Uncommitted changes exist. Commit or discard them, then run the app again:")
        print("\n".join(f"    {line}" for line in before))
        return

    if command(["git", "checkout", MAIN_BRANCH], cwd=repo).returncode:
        ui_fail(f"Could not switch to {MAIN_BRANCH}.")
        return

    old_sessions = session_branches(repo)
    if old_sessions:
        ui_info(f"Found {len(old_sessions)} old session branch(es). They are left untouched.")

    if main_is_ahead(repo):
        ui_warn(f"{MAIN_BRANCH} has commits from an earlier run that were never pushed. Pushing them first.")
        if not push_with_retry(repo, f"Pushing {MAIN_BRANCH}"):
            ui_warn("Still cannot reach GitHub. Fix the connection and run the app again.")
            return

    log(f"===== Session started | repo={repo} | topic={subject} | commits={limit} | direct to {MAIN_BRANCH}")

    completed = 0
    failures = 0
    try:
        while completed < limit:
            cycle = completed + 1
            target = pick_target(cycle)

            # Git check 1: start from a clean tree so each commit only contains generated changes.
            before = git_changes(repo)
            if before is None:
                return
            if before:
                ui_fail("Uncommitted changes appeared mid-run. Stopping.")
                print("\n".join(f"    {line}" for line in before))
                return

            # Read the current file, or start fresh if it's new.
            file_path = repo / target
            is_new = not file_path.exists()
            current = "" if is_new else file_path.read_text(encoding="utf-8")
            if target.endswith(".md") and not current.strip():
                current = f"# {subject}\n"
            if (RESET_UNMARKED_OVERSIZE and not is_new and AUTO_START not in current
                    and len(current.encode("utf-8")) > LEGACY_MAX_BYTES):
                ui_warn(f"{target} is large and has no markers; resetting it to a fresh page.")
                log(f"Reset unmarked oversize file {target}")
                current = fresh_file(target, subject)

            action = "Create" if is_new else "Edit"
            ui_step(cycle, limit, f"{action} {target}")
            new_content = generate_content(subject, target, current, cycle)
            if not new_content or new_content == current:
                failures += 1
                ui_warn("No usable change this round.")
                log(f"Round {cycle}/{limit}: {target} generator produced no change")
                if failures >= 3:
                    ui_fail("Stopping after 3 failed rounds.")
                    return
                continue
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(new_content, encoding="utf-8")
            ui_info("Generated new content")
            pause("after edit")

            # Git check 2: confirm Git actually sees a change in the target file.
            changed = git_changes(repo, target)
            if changed is None:
                return
            if not changed:
                ui_warn("Git sees no change in the file; skipping.")
                log(f"Round {cycle}/{limit}: Git saw no change, skipped")
                continue

            # Show a short preview of the changed lines; the full list goes to the log.
            diff = show_diff(current, new_content)
            print(paint(f"    {len(diff)} line(s) changed", "dim"))
            for line in diff[:12]:
                print("    " + paint(line, "green" if line.startswith("+") else "red"))
            if len(diff) > 12:
                print(paint(f"    … {len(diff) - 12} more (see commit-cycle.log)", "dim"))
            log(f"----- Round {cycle}/{limit} generated content for {target} ({len(diff)} lines)")
            log("\n".join(diff))

            staged = command(["git", "add", "--", target], cwd=repo)
            if staged.returncode:
                ui_fail("Could not stage the file.")
                print(staged.stderr)
                log(f"git add failed: {staged.stderr}")
                return
            verb = "add" if is_new else "update"
            message = f"{COMMIT_PREFIX} {verb} {target} ({cycle}/{limit})"
            committed = command(["git", "commit", "-m", message], cwd=repo)
            if committed.returncode:
                ui_fail("Commit failed.")
                print(committed.stderr or committed.stdout)
                log(f"git commit failed: {committed.stderr or committed.stdout}")
                return
            ui_ok(f"Committed: {message}")
            log(committed.stdout.strip())
            pause("after commit")

            if not push_with_retry(repo, "Pushing commit", progress=(completed, limit)):
                ui_warn(f"The commit is saved locally on {MAIN_BRANCH}. Run the app again and it will push it first.")
                return
            completed += 1
            failures = 0
            print("  " + progress_bar(completed, limit))
            if completed < limit:
                pause("after push")
        ui_ok(f"Finished: {completed} commits pushed to {MAIN_BRANCH}")
        log(f"===== Session finished: {completed} commits pushed to {MAIN_BRANCH}")
    except KeyboardInterrupt:
        ui_warn(f"Stopped after {completed}/{limit} commits. Commits made so far are on {MAIN_BRANCH}.")
        log(f"===== Session stopped by user after {completed}/{limit} commits")
    ui_panel("Result", [
        f"Commits pushed   {completed}/{limit}",
        f"Branch           {MAIN_BRANCH}",
        "Each change is its own commit",
    ], style="green" if completed else "yellow")


# ---------- Startup ----------

def ui_warning_box(title: str, paragraphs: list[str], width: int = 72) -> None:
    inner = width - 4
    border = "═" * (width - 2)
    edge = lambda text: paint(text, "red", "bold")
    print(edge(f"╔{border}╗"))
    print(edge(f"║ {title.center(inner)} ║"))
    print(edge(f"╠{border}╣"))
    for paragraph in paragraphs:
        for line in textwrap.wrap(paragraph, inner):
            print(edge("║ ") + line.ljust(inner) + edge(" ║"))
        print(edge("║") + " " * (width - 2) + edge("║"))
    print(edge(f"╚{border}╝"))


def confirm_warning() -> bool:
    print()
    ui_warning_box(WARNING_TITLE, WARNING_PARAGRAPHS)
    print()
    print(paint(f"  To continue, type exactly:  {WARNING_CONFIRM_PHRASE}", "bold", "yellow"))
    answer = input(f"  {paint('›', 'red')} ").strip()
    return answer == WARNING_CONFIRM_PHRASE


def main() -> None:
    os.system("cls" if os.name == "nt" else "clear")
    ui_header("COMMIT CYCLE", "Generate  →  commit  →  push, on repeat")
    if not confirm_warning():
        ui_info("Cancelled. Nothing was changed.")
        return

    ui_step(1, 2, "Checking tools and GitHub sign-in")
    ensure_tools()
    sign_in()
    ensure_identity()

    while True:
        ui_step(2, 2, "Choose a repository")
        print(f"    {paint('1', 'bold')}  Create a new GitHub repository")
        print(f"    {paint('2', 'bold')}  Use an existing local Git repository")
        selection = ask("Choose 1 or 2")
        if selection == "1":
            repo = create_repository()
        elif selection == "2":
            repo = choose_existing_repository()
        else:
            ui_warn("Choose 1 or 2.")
            continue
        if repo is None:
            ui_info("Returning to the main selection…")
            continue
        generate_loop(repo)
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
