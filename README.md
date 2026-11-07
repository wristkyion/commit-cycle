# Commit Cycle

A terminal workflow for creating a GitHub repository or continuing with an existing local repository, then repeating the edit, commit, and push cycle.

## Run

From PowerShell in this folder:

```powershell
py app.py
```

The program checks for Git and GitHub CLI (`gh`) and installs missing tools with `winget`. It signs in with GitHub CLI device authentication; the terminal displays the one-time code and URL if a browser does not open. Choose to create a new repository or provide the path to an existing local Git repository. The new-repository flow asks for a name, parent folder, and visibility, then creates an initial README commit. The project folder is created as `<parent folder>\<repository name>`; if you enter a folder already named after the project, the app uses it directly when possible. If the destination folder exists, the program offers to use it, return to the menu, or delete it after a warning and exact `DELETE` confirmation. If the GitHub name already exists, it offers to link and push to that repository, choose another name, or return to the menu.

After selecting a repository, choose automatic watch mode or manual commits. Watch mode asks for 5–50 commits, then waits for files to be created, edited, or deleted in the project folder. After changes settle, it creates a descriptive commit and pushes it, then waits for the next change. Press Ctrl+C to stop early. Manual mode lets you enter each commit message. Network operations show a spinner. If a push fails after a commit was created, use the existing-repository option on the next run and retry pushing existing commits.

Requirements: Windows with Python 3 and `winget` (App Installer).
