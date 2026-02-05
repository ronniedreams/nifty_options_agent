# Git SOP for Isolated Experiment Testing (Trading Systems)

## Goal

Safely develop, test, and promote trading system features without mixing experiments, without confusion, and with full rollback clarity during limited market hours.

---

## Branch Philosophy (Very Important)

### Long-lived
- **main** → Stable, trusted, production truth
  - EC2 runs only this
  - Never experimental
  - Always safe

### Short-lived (Disposable)
- **feature/*** → Feature development branches
- **draft/*** → Isolated draft builds for market testing

**Rule:** Only main lives long. Everything else can be deleted without regret.

---

## Branch Meanings (Memorize This)

| Branch | Meaning |
|--------|---------|
| `main` | "This code is safe." Production only. |
| `feature/X` | "I am building feature X." Development, can break things. |
| `draft/X` | "This is main + X, temporarily, for testing." Pre-market snapshot. |

---

## Standard Workflow (Always Follow This)

### Step 1: Start a New Feature

```bash
git checkout main
git pull origin main
git checkout -b feature/feature-X
```

**Rules:**
- Work freely on feature branch
- Commit normally
- Break things if needed - this is development
- Feature is isolated from everything else

### Step 2: Create an Isolated Draft for Testing

```bash
git checkout main
git checkout -b draft/feature-X
git merge feature/feature-X
```

**Result:**
- `draft/feature-X` = `main` + `feature-X`
- No other features included
- Guaranteed isolation for testing

### Step 3: Pre-Market Freeze (Before 9:15 AM)

```bash
git tag pre-market-YYYYMMDD-feature-X
git push origin draft/feature-X
git push origin --tags
```

**Rules:**
- ❌ No code changes during market hours (9:15 AM - 3:30 PM IST)
- 👀 Observe only (logs, behavior, trades)
- Tag captures exact state before market opens
- Enables rollback if needed

### Step 4: Post-Market Decision (After 3:30 PM)

#### ✅ If Feature Worked Well

```bash
git checkout main
git pull origin main
git merge feature/feature-X
git tag stable-YYYYMMDD-feature-X
git push origin main
git push origin --tags

# Cleanup
git branch -D feature/feature-X
git branch -D draft/feature-X
git push origin --delete feature/feature-X draft/feature-X
```

Feature is now officially part of production.

#### ❌ If Feature Failed

```bash
git branch -D draft/feature-X
git branch -D feature/feature-X
git push origin --delete draft/feature-X feature/feature-X
```

Nothing else is affected. `main` remains clean. Delete and move on.

---

## Testing Multiple Features (No Confusion)

Each feature gets its own isolated draft:

```
feature/exp1  →  draft/exp1
feature/exp2  →  draft/exp2
feature/exp3  →  draft/exp3
```

**Rules:**
- Never mix drafts unless intentionally testing combinations
- One draft = one idea = one test
- Each draft is purely main + one feature

---

## Hard Rules (Non-Negotiable)

❌ **Never test directly on main**
❌ **Never stack multiple features in one draft**
❌ **Never change code during market hours** (9:15 AM - 3:30 PM IST)
❌ **Never delete tags** (tags = time machines)
❌ **Never "fix quickly" on EC2** (use feature/draft workflow)

---

## Rollback & Debugging

### To Inspect What Ran on a Given Day

```bash
git tag -l | grep pre-market-YYYYMMDD
git checkout pre-market-YYYYMMDD-feature-X
```

### To Revert Production

```bash
git checkout stable-YYYYMMDD-feature-X
git push origin main --force-with-lease  # Only if absolutely necessary
```

**Tags = time machine. Use them.**

---

## Cleanup Discipline (Weekly)

```bash
# List all draft branches
git branch --list "draft/*"
git branch --list "feature/*"

# If a branch has no future → delete it
git branch -D draft/old-feature
git branch -D feature/old-feature
git push origin --delete draft/old-feature feature/old-feature
```

---

## Mental Model (Pin This)

**feature builds ideas, draft tests ideas, main trusts ideas.**

---

## Why This SOP Works

✅ True isolation - No surprise dependencies
✅ Zero ambiguity - Branch name tells you everything
✅ No resets - History is never rewritten
✅ No branch state confusion - Clear progression
✅ Perfect for limited market windows - Pre-market snapshot
✅ Matches professional release engineering - Industry standard
✅ Full rollback capability - Tags as restore points
✅ Safety by design - Mistakes are contained

---

## Three-Way Sync (Laptop ↔ GitHub ↔ EC2)

### Before Starting Feature

```bash
# Laptop: Start fresh from main
git checkout main
git pull origin main

# EC2: Keep on main (production)
# DO NOT pull feature or draft branches to EC2
```

### During Development (Before Market)

```bash
# Laptop: Work on feature/X and draft/X
git push origin feature/feature-X
git push origin draft/feature-X
git push origin --tags

# EC2: Stays on main (no changes)
```

### Post-Market Merge (After 3:30 PM)

```bash
# Laptop: Merge feature/X to main if successful
git checkout main
git pull origin main
git merge feature/feature-X
git push origin main
git push origin --tags

# EC2: Pull latest main
ssh -i "key.pem" ubuntu@EC2_IP
cd ~/nifty_options_agent
git checkout main
git pull origin main
docker-compose down && docker-compose up -d --build
```

---

## Common Scenarios

### Scenario 1: Test New Filter Logic

```bash
# 1. Start experiment
git checkout main && git pull
git checkout -b feature/new-filter

# 2. Edit continuous_filter.py, commit, test locally
git commit -am "Implement new filter logic"

# 3. Create draft before market
git checkout main
git checkout -b draft/new-filter
git merge feature/new-filter
git tag pre-market-20260128-new-filter
git push origin draft/new-filter --tags

# 4. Deploy draft to EC2 (if needed for testing)
# (Only if you want to test live market data)

# 5. Post-market decision
# If good: merge to main
# If bad: delete both branches
```

### Scenario 2: Quick Hotfix on Main

**Rule:** Only do this if absolutely critical during market hours.

```bash
# DO NOT: Branch from main and expect to keep it
# INSTEAD: Handle in post-market feature workflow

# Example: Bug found at 2 PM
# → Log it
# → Create feature/bugfix-X after market
# → Test in draft/bugfix-X pre-market next day
```

### Scenario 3: Failed Feature

```bash
# If draft fails during pre-market testing
git checkout main
git branch -D draft/feature-X
git branch -D feature/feature-X
git push origin --delete draft/feature-X feature/feature-X

# main is untouched. Try again tomorrow.
```

---

## EC2 Deployment Safety

**EC2 ONLY runs main branch. Period.**

```bash
# Safe EC2 workflow
git checkout main
git pull origin main
docker-compose down
docker-compose build
docker-compose up -d
```

Never deploy draft or feature branches to EC2 (unless explicitly testing specific market data, which is documented separately).

---

## Tags Naming Convention

### Standard Release Tags (After Market Close)

For general stable releases (no specific feature):
```
stable-YYYY-MM-DD
```

Examples:
- `stable-2026-02-05` (general release after testing)
- `stable-2026-02-06` (daily stable snapshot)

### Feature-Specific Tags (During Development)

For pre-market testing and feature releases:
```
pre-market-YYYYMMDD-feature-name
stable-YYYYMMDD-feature-name
```

Examples:
- `pre-market-20260128-new-filter`
- `stable-20260128-new-filter`
- `pre-market-20260129-market-order-fix`
- `stable-20260129-market-order-fix`

**When to use which:**
- Use `stable-YYYY-MM-DD` for daily stable releases (recommended for production)
- Use `stable-YYYYMMDD-feature-X` when you want to mark a specific feature as stable

---

## Weekly Checklist

- [ ] Review `git branch -a` - are there old feature/* branches?
- [ ] Delete completed features
- [ ] Check `git tag -l` - is history preserved?
- [ ] Verify main is at latest stable-* tag
- [ ] EC2 is running latest main
- [ ] Backup .env and live_state.db (not in git)
- [ ] Document any failed features (lessons learned)

---

## Questions to Ask Before Any Code Change

1. **Is this on main or feature/?** → Should be feature/X for new features
2. **Is market open?** → No code changes during 9:15 AM - 3:30 PM
3. **Have I created a draft/** → Needed before testing in market
4. **Is this pushed to GitHub?** → Required for rollback capability
5. **Can I delete this branch?** → If yes, it's expendable (good sign)
6. **Is EC2 updated after merge?** → Required for production sync

---

---

## ⚠️ MANDATORY Commit Workflow (Claude Code)

**NEVER commit code without following these steps in order:**

1. **Show implementation first**
   - Present all code changes to the user
   - Explain what was changed and why
   - Wait for user feedback

2. **Get approval to run /pre-commit checks**
   - Ask: "Should I run /pre-commit checks on these changes?"
   - Wait for explicit permission

3. **Run /pre-commit checks**
   - Execute: `/pre-commit`
   - Review all check results
   - Fix any issues found

4. **Ask for permission to commit**
   - Ask: "All checks passed. Should I commit these changes?"
   - Provide commit message preview
   - Wait for explicit permission

5. **Commit only if given permission**
   - Only after explicit "yes" from user
   - Never rush to commit
   - Never skip steps 1-4

**Why This Matters:**
- Prevents accidental commits without review
- Ensures quality checks are run
- Gives user control over git history
- Avoids rushing to commit without approval

**Example Flow:**
```
[After implementing changes]
"I've updated continuous_filter.py to fix tie-breaker logic. Here are the changes:
[show code diff]

Should I run /pre-commit checks on these changes?"

[User: yes]
[Run /pre-commit]

"All checks passed:
✓ Code review: No issues
✓ Integration check: No cross-module impacts
✓ Tests: All passed

Should I commit with message: 'Fix: Tie-breaker now prefers strikes multiple of 100'?"

[User: yes]
[Commit and push]
```

---

## Contact Points

- **Workflow questions:** See this document
- **Git issues:** Check `git log --oneline` and `git tag -l`
- **Rollback needed:** Use tag names to restore state
- **Failed test:** Delete branches, review logs, try again next market

---

## Version

Created: 2026-01-28
Last Updated: 2026-01-28
Version: 1.0

**Status:** ACTIVE - Follow this SOP for all future development
