# Publish safely to a public GitHub repository

These steps prepare a **new** repository. They do not automatically upload anything. Git/`gh` were not available on PATH in the preparation environment; install them locally as needed. Use your real account/repository values only at the remote-creation step.

## 1. Prepare the public surface

1. Read the rewritten root README: clear benefit-led intro, accurate feature table, install/build steps and safety/licensing caveats.
2. Capture **reviewed** screenshots using [SCREENSHOTS.md](SCREENSHOTS.md). Do not copy `artifacts/` into a public folder wholesale.
3. Keep private configuration in ignored `.env` / `config.local.*` / local state folders, not source modules. This app needs no cloud API key to run.
4. Review PyQt6 GPLv3/commercial/Qt licensing before distributing binaries. Publishing your own source under MIT does not override those terms.
5. Publish source in Git; put reviewed, signed/versioned distribution ZIPs and checksums in **Releases**, not commits.

Suggested repository name: `TATClean`. Suggested description:

> Safety-first Windows cleaning and reversible gaming controls, built with Python and PyQt6.

Suggested topics: `windows`, `pyqt6`, `python`, `system-cleaner`, `gaming`, `system-maintenance`, `timer-resolution`. Avoid unsupported "guaranteed FPS boost" claims.

## 2. Install Git / optional GitHub CLI

If missing, run in your own PowerShell session:

```powershell
winget install --id Git.Git --exact
winget install --id GitHub.cli --exact
```

Open a new terminal afterward and verify:

```powershell
git --version
gh --version
```

Do not install tools or change system permissions just to work around a managed-PC policy without administrator approval.

## 3. Create the first local commit

Run **one block at a time**, checking output before continuing. If this directory is already a Git repository with user history, inspect it instead of replacing/resetting it.

```powershell
Set-Location 'C:\Users\Your_Name\Desktop\TATClean'
git init
git branch -M main
```

Use the GitHub **noreply** email shown in your GitHub email settings if you do not want to publish a personal address in commit metadata:

```powershell
$CommitName = Read-Host 'Public commit author name'
$CommitEmail = Read-Host 'GitHub noreply email from Settings > Emails'
git config --local user.name "$CommitName"
git config --local user.email "$CommitEmail"
```

Review ignore rules before staging. This confirms paths are ignored without printing their contents:

```powershell
git check-ignore -v .env .venv/pyvenv.cfg dist/TATClean/TATClean.exe artifacts/ui-gaming.png state.sqlite docs/media/raw/capture.png
git status --short --untracked-files=all
```

Stage an explicit source/documentation allowlist, not `git add .`:

```powershell
git add -- .gitignore README.md LICENSE THIRD_PARTY_NOTICES.md pyproject.toml run.py TATClean.spec build.ps1
git add -- requirements.txt requirements-dev.txt requirements-lock.txt requirements-dev.lock.txt
git add -- src tests tools assets docs installer .github
git diff --cached --name-status
git diff --cached --check
```

### Secret and privacy gate — before committing

The included helper checks staged filenames/content, fails on suspect material, and **never prints matched secret values**:

```powershell
.\.venv\Scripts\python.exe tools\check_publication.py
if ($LASTEXITCODE -ne 0) { throw 'Publication audit failed. Stop and fix the staged files.' }
```

Install a current Gitleaks release from its [official releases](https://github.com/gitleaks/gitleaks/releases) and run an independent scan:

```powershell
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Git/index validation failed; no secret scan can be trusted yet.' }
gitleaks git --staged --redact
if ($LASTEXITCODE -ne 0) { throw 'Secret scan did not pass. Do not commit or push.' }
git diff --cached --stat
git diff --cached
```

Neither regex checks nor Gitleaks guarantee the absence of every secret. Manually review screenshots, account/file paths, tokens, private certificates, exported diagnostics and process/network details. Do not dismiss a failure by bypassing hooks or push protection.

If a private file was accidentally staged, unstage it **without deleting your local copy**. Before the first commit:

```powershell
git rm --cached -- path/to/private-file
```

For a directory use `git rm -r --cached -- path/to/private-directory`. For a file changed after an existing commit use `git restore --staged -- path/to/file`. `.gitignore` does not remove already tracked material or erase history. Never use `git reset --hard`, `git clean -fd` or a broad checkout to fix a staging mistake.

When the review passes:

```powershell
git commit -m 'Initial public release: TAT Clean 1.1.0'
git status --short
```

If a real credential was ever committed, **revoke/rotate it first**, then follow GitHub's [sensitive-data removal guide](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository). Merely deleting it in a later commit is insufficient. Coordinate any history rewrite; do not force-push over collaborators' work.

## 4. Create an empty public repository and push

### GitHub website + Git (explicit remote workflow)

On GitHub create a new **public** repository named `TATClean`. Leave **Initialize with README**, **Add .gitignore** and **Choose a license** unchecked; those files are already local.

```powershell
$Owner = Read-Host 'GitHub account or organization that owns the empty repository'
$Repo = 'TATClean'
git remote add origin "https://github.com/$Owner/$Repo.git"
git remote -v
git push --dry-run origin main
git push -u origin main
```

Authenticate through Git Credential Manager's browser flow. GitHub does not accept an account password for HTTPS Git pushes. Never put a PAT in the remote URL, a committed file or a shell command that will remain in history.

If `origin` already exists, run `git remote -v` and inspect it. Only after verifying the intended owner/repository use `git remote set-url origin "https://github.com/$Owner/$Repo.git"`. A rejected push is not permission to force-push: inspect the remote/history and resolve it deliberately.

### GitHub CLI alternative (do not run both remote-creation paths)

After the same staging/security review and first commit:

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
$Owner = Read-Host 'GitHub owner'
$Repo = 'TATClean'
gh repo create "$Owner/$Repo" --public --source . --remote origin --description 'Safety-first Windows cleaning and reversible gaming controls, built with Python and PyQt6.'
git remote -v
git push --dry-run origin main
git push -u origin main
```

The public-repository command changes visibility: run it only after approving exactly what will be public. Credentials stay in the CLI/OS credential store, not the project.

## 5. Finish the GitHub presentation

- Verify the uploaded file list and rendered README; then add reviewed gallery images and uncomment their README block.
- Add description/topics, enable available secret-scanning/push-protection features, and protect `main` with reviews/required CI checks when appropriate.
- Replace the README's generic Releases instruction with a real release link/CI badge once the owner/repository exists. Do not ship broken owner placeholders.
- Wait for the Windows validation workflow. Do not claim a passing CI badge until it actually passes on GitHub.
- Review public issue attachments and CI artifacts: fixture/source tests are safe, but private captures/logs are not automatically safe.

## 6. Publish a binary release separately

Rebuild after the README/source changes; older ZIPs still contain their older source/docs. Review signing and licensing, then upload the entire Windows ZIP, application-source ZIP and checksums:

```powershell
.\build.ps1
git tag -a v1.1.0 -m 'TAT Clean 1.1.0'
git push origin v1.1.0
gh release create v1.1.0 dist/TATClean-1.1.0-windows-x64.zip dist/TATClean-1.1.0-source.zip dist/SHA256SUMS.txt --verify-tag --title 'TAT Clean 1.1.0' --notes 'Windows cleaning and reversible gaming controls. Review safety, signature status and third-party licensing before elevation.'
```

Skip existing tags/releases rather than overwriting them. Signing changes binary bytes: repackage/recalculate checksums after signing. The commands above are an explicit publication step, not an automatic part of development or `build.ps1`.

Official reference: [GitHub: adding locally hosted code](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github).
