# Commit Cycle

A Windows terminal tool that makes small placeholder edits to a Git repository, commits each one, and pushes it to GitHub on a timer.

> **Warning: read before use.** Every commit this tool creates is a real commit under your GitHub name. Anyone who views your profile, contribution graph, or repository history can see them, and they may assume the commits reflect real work. Use it only on test repositories or accounts where that is acceptable. Do not use it to make your activity look like real progress. GitHub's Acceptable Use Policies prohibit inauthentic activity, and accounts that break them can be restricted or suspended.

## What it does

Each round of a run does the following:

1. Checks that the working tree is clean.
2. Generates new content for the next file in the rotation.
3. Shows a preview of the changed lines and writes the full diff to `commit-cycle.log`.
4. Commits only that file on `main`, with a `chore(auto):` prefix.
5. Pushes to `origin`, retrying up to 5 times if the push fails.
6. Waits a few seconds between steps and repeats until the chosen number of commits is reached.

The rotation repeats every five commits:

| Commit | File |
|---|---|
| 1, 6, 11, … | `index.html` (sections, cards, FAQ, stats, scripts, or styles added between markers) |
| 2, 7, 12, … | `README.md` (a new section added between markers) |
| 3, 8, 13, … | `generated/module_N.py` (a new file each time) |
| 4, 9, 14, … | `generated/style_N.css` (a new file each time) |
| 5, 10, 15, … | `generated/script_N.js` (a new file each time) |

Generated text is built from word lists and templates in Python. No AI service or API key is used.

## Requirements

- Windows 10 or 11. Missing Git and GitHub CLI are installed with `winget`.
- Python 3.9 or newer, started with the `py` launcher.
- Git.
- GitHub CLI (`gh`), signed in to your account.
- A local repository with an `origin` remote, or a new one created through the menu.

## Usage

```powershell
cd C:\path\to\commit-cycle
py app.py
```

The program then:

1. Shows the warning and asks you to type the confirmation phrase.
2. Checks Git and GitHub sign-in, and asks for your Git name and email if they are missing.
3. Asks whether to create a new repository or use an existing local one.
4. Asks how many commits to make (1 to 200).

Press `Ctrl+C` at any time to stop. Commits already made stay on `main`.

## Hiding generated commits from your history

Generated commits use the `chore(auto):` prefix, so you can filter them in Git:

```powershell
git log --invert-grep --grep="chore(auto)" --oneline   # hide generated commits
git log --grep="chore(auto)" --oneline                  # show only generated commits
```

Filtering only changes what `git log` displays. It does not remove or rewrite commits.

## Settings

Edit these constants near the top of `app.py`:

| Setting | Default | Purpose |
|---|---|---|
| `DELAY_SECONDS` | `3` | Pause after each edit, commit, and push |
| `COMMIT_PREFIX` | `chore(auto):` | Prefix on every generated commit message |
| `MAX_GENERATED_BLOCKS` | `8` | Generated blocks kept in `index.html` and `README.md`; older ones are removed |
| `LEGACY_MAX_BYTES` | `4000` | Files without markers that are larger than this are reset once |
| `RESET_UNMARKED_OVERSIZE` | `True` | Set to `False` to stop the one-time reset of large unmarked files |
| `WARNING_CONFIRM_PHRASE` | `I UNDERSTAND AND ACCEPT THIS RISK` | Phrase required at startup |

## Limitations

- The generated text is placeholder content. It doesn't describe your project.
- Commits go directly to `main`. If the branch is protected, pushes will be refused.
- The tool stops if it finds uncommitted changes, so it never discards your work.
- It never force-pushes.
- If a push fails, the commits stay on your local `main`. The next run pushes them before generating anything new.
- Old session branches from earlier versions are left alone. Delete them with `git push origin --delete <branch>` if you no longer need them.

## Logs

Each run appends to `commit-cycle.log`, which is created next to `app.py`. The log includes timestamps, the full list of changed lines for each round, and the output of every Git push. Add `commit-cycle.log` to `.gitignore` if you keep this project in a repository.

## Files this tool creates

In the target repository, the tool creates or edits `index.html`, `README.md`, and the `generated/` folder. It only touches files in its rotation and never modifies other files.

## Responsible use

Use this tool only for testing, learning Git workflows, or demonstrating the commit cycle on repositories where everyone involved knows the commits are generated. Don't use it on a profile or repository that others will read as evidence of your work.
