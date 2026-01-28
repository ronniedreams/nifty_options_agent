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
- **experiment/*** → Feature development branches
- **draft/*** → Isolated draft builds for market testing

**Rule:** Only main lives long. Everything else can be deleted without regret.

---

## Branch Meanings (Memorize This)

| Branch | Meaning |
|--------|---------|
| `main` | "This code is safe." Production only. |
| `experiment/X` | "I am building feature X." Development, can break things. |
| `draft/X` | "This is main + X, temporarily, for testing." Pre-market snapshot. |

---

## Standard Workflow (Always Follow This)

### Step 1: Start a New Feature

```bash
git checkout main
git pull origin main
git checkout -b experiment/feature-X
```

**Rules:**
- Work freely on experiment branch
- Commit normally
- Break things if needed - this is development
- Experiment is isolated from everything else

### Step 2: Create an Isolated Draft for Testing

```bash
git checkout main
git checkout -b draft/feature-X
git merge experiment/feature-X
```

**Result:**
- `draft/feature-X` = `main` + `feature-X`
- No other experiments included
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
git merge experiment/feature-X
git tag stable-YYYYMMDD-feature-X
git push origin main
git push origin --tags

# Cleanup
git branch -D experiment/feature-X
git branch -D draft/feature-X
git push origin --delete experiment/feature-X draft/feature-X
```

Feature is now officially part of production.

#### ❌ If Feature Failed

```bash
git branch -D draft/feature-X
git branch -D experiment/feature-X
git push origin --delete draft/feature-X experiment/feature-X
```

Nothing else is affected. `main` remains clean. Delete and move on.

---

## Testing Multiple Experiments (No Confusion)

Each experiment gets its own isolated draft:

```
experiment/exp1  →  draft/exp1
experiment/exp2  →  draft/exp2
experiment/exp3  →  draft/exp3
```

**Rules:**
- Never mix drafts unless intentionally testing combinations
- One draft = one idea = one test
- Each draft is purely main + one experiment

---

## Hard Rules (Non-Negotiable)

❌ **Never test directly on main**
❌ **Never stack multiple experiments in one draft**
❌ **Never change code during market hours** (9:15 AM - 3:30 PM IST)
❌ **Never delete tags** (tags = time machines)
❌ **Never "fix quickly" on EC2** (use experiment/draft workflow)

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
git branch --list "experiment/*"

# If a branch has no future → delete it
git branch -D draft/old-feature
git branch -D experiment/old-feature
git push origin --delete draft/old-feature experiment/old-feature
```

---

## Mental Model (Pin This)

**experiment builds ideas, draft tests ideas, main trusts ideas.**

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

### Before Starting Experiment

```bash
# Laptop: Start fresh from main
git checkout main
git pull origin main

# EC2: Keep on main (production)
# DO NOT pull experiment or draft branches to EC2
```

### During Development (Before Market)

```bash
# Laptop: Work on experiment/X and draft/X
git push origin experiment/feature-X
git push origin draft/feature-X
git push origin --tags

# EC2: Stays on main (no changes)
```

### Post-Market Merge (After 3:30 PM)

```bash
# Laptop: Merge experiment/X to main if successful
git checkout main
git pull origin main
git merge experiment/feature-X
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
git checkout -b experiment/new-filter

# 2. Edit continuous_filter.py, commit, test locally
git commit -am "Implement new filter logic"

# 3. Create draft before market
git checkout main
git checkout -b draft/new-filter
git merge experiment/new-filter
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
# INSTEAD: Handle in post-market experiment workflow

# Example: Bug found at 2 PM
# → Log it
# → Create experiment/bugfix-X after market
# → Test in draft/bugfix-X pre-market next day
```

### Scenario 3: Failed Experiment

```bash
# If draft fails during pre-market testing
git checkout main
git branch -D draft/feature-X
git branch -D experiment/feature-X
git push origin --delete draft/feature-X experiment/feature-X

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

Never deploy draft or experiment branches to EC2 (unless explicitly testing specific market data, which is documented separately).

---

## Tags Naming Convention

```
pre-market-YYYYMMDD-feature-name
stable-YYYYMMDD-feature-name
```

Examples:
- `pre-market-20260128-new-filter`
- `stable-20260128-new-filter`
- `pre-market-20260129-market-order-fix`
- `stable-20260129-market-order-fix`

---

## Weekly Checklist

- [ ] Review `git branch -a` - are there old experiment/* branches?
- [ ] Delete completed experiments
- [ ] Check `git tag -l` - is history preserved?
- [ ] Verify main is at latest stable-* tag
- [ ] EC2 is running latest main
- [ ] Backup .env and live_state.db (not in git)
- [ ] Document any failed experiments (lessons learned)

---

## Questions to Ask Before Any Code Change

1. **Is this on main or experiment/?** → Should be experiment/X for new features
2. **Is market open?** → No code changes during 9:15 AM - 3:30 PM
3. **Have I created a draft/** → Needed before testing in market
4. **Is this pushed to GitHub?** → Required for rollback capability
5. **Can I delete this branch?** → If yes, it's expendable (good sign)
6. **Is EC2 updated after merge?** → Required for production sync

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
