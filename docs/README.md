# NIFTY Options Trading System - Documentation

## Documentation Structure

This folder contains all project documentation organized by category.

```
docs/
├── README.md                    # This file - documentation index
└── implementation/              # Implementation notes and summaries
    ├── README.md                # Implementation folder index
    └── 2026-01-24_profit_booking_market_orders.md
```

---

## Core Documentation (Root Level)

These files provide comprehensive theory and architecture documentation.

### Theory Documents

| File | Location | Purpose |
|------|----------|---------|
| **CLAUDE.md** | Root | Main architecture, system overview, quick context |
| **SWING_DETECTION_THEORY.md** | `baseline_v1_live/` | Watch-based swing detection mechanism |
| **STRIKE_FILTRATION_THEORY.md** | `baseline_v1_live/` | Three-stage filter pipeline (static, dynamic, tie-breaker) |
| **ORDER_EXECUTION_THEORY.md** | `baseline_v1_live/` | Proactive SL orders, position sizing, daily exits |

### Setup & Operations

| File | Location | Purpose |
|------|----------|---------|
| **DAILY_STARTUP.md** | Root | Daily pre-market checklist |
| **PRE_LAUNCH_CHECKLIST.md** | Root | Pre-launch validation steps |
| **TELEGRAM_SETUP.md** | Root | Telegram bot configuration |

### Agent & Skill Guides

| File | Location | Purpose |
|------|----------|---------|
| **SUB_AGENTS_REFERENCE.md** | `.claude/` | Complete agent documentation |
| **skills/** | `.claude/skills/` | Slash command skill definitions |

---

## Implementation Notes

Located in `docs/implementation/`

These documents capture detailed implementation summaries for major features and bug fixes. Each file:
- Documents what was changed and why
- Provides testing checklist
- Includes deployment guide
- Serves as historical record

**Current Notes:**
- **2026-01-24:** Profit Booking Market Orders - Fixed +5R exit to actually close positions at broker

---

## Quick Reference by Use Case

### "I want to understand how the system works"
1. Start: **CLAUDE.md** (30-second overview, 3-minute architecture)
2. Deep dive: **SWING_DETECTION_THEORY.md**, **STRIKE_FILTRATION_THEORY.md**, **ORDER_EXECUTION_THEORY.md**

### "I'm deploying to production"
1. **PRE_LAUNCH_CHECKLIST.md** - Validate system
2. **DAILY_STARTUP.md** - Daily routine
3. **CLAUDE.md** (EC2 Deployment section)

### "I need to debug an issue"
1. Check theory docs for expected behavior
2. Check `.claude/SUB_AGENTS_REFERENCE.md` for which agent handles that domain
3. Check implementation notes for recent changes

### "I want to modify the system"
1. Read relevant theory doc (understand current logic)
2. Check `.claude/rules/` for constraints
3. After implementation, create summary in `docs/implementation/`

### "I'm testing a new feature"
1. Check theory doc for testing checklist
2. Review implementation summary for test scenarios
3. Follow testing protocol in **DAILY_STARTUP.md**

---

## Documentation Standards

### Theory Documents
- Focus on **WHY** and **HOW**, not just what
- Include examples and edge cases
- Provide troubleshooting section
- Keep synchronized with code changes

### Implementation Notes
- Named: `YYYY-MM-DD_feature_name.md`
- Include: Problem, Solution, Files Changed, Testing Checklist
- Created after significant features/fixes
- Stored in `docs/implementation/`

### Code Comments
- Minimal inline comments (code should be self-explanatory)
- Use docstrings for classes and complex functions
- Log messages should reference theory docs when relevant

---

## Maintaining Documentation

### When to Update Docs

**Theory Docs:**
- Algorithm changes (swing detection, filters, position sizing)
- New order types or execution strategies
- Configuration parameter changes
- Add new troubleshooting scenarios

**Implementation Notes:**
- After any feature spanning 3+ files
- Critical bug fixes (like +5R exit issue)
- Performance optimizations
- Database schema changes

**Agent/Skill Docs:**
- New agents or skills added
- Agent responsibilities change
- Cross-agent workflows modified

### Documentation Review Checklist

Before deploying to production:
- [ ] Theory docs reflect current code behavior
- [ ] Implementation summary created (if applicable)
- [ ] Testing checklist updated
- [ ] Troubleshooting section includes new issues
- [ ] Configuration parameters documented
- [ ] Examples match current code

---

## Contributing to Documentation

### Adding New Theory Docs

1. Create in appropriate location (root or `baseline_v1_live/`)
2. Follow existing structure: Overview → Core Concept → Implementation → Examples → Troubleshooting
3. Update this README.md with new file reference
4. Cross-link from related docs

### Adding Implementation Notes

1. Create in `docs/implementation/` with format `YYYY-MM-DD_feature_name.md`
2. Use template from existing notes
3. Update `docs/implementation/README.md`
4. Reference in related theory docs if behavior changes

### Updating Existing Docs

1. Keep git history (don't delete old sections, mark as deprecated if needed)
2. Add "Updated: YYYY-MM-DD" note for major changes
3. Update cross-references in other docs
4. Test examples still work with current code

---

## Documentation Tools

### Search Documentation
```bash
# Search all markdown files
grep -r "swing detection" *.md baseline_v1_live/*.md .claude/*.md docs/**/*.md

# Find references to a function
grep -r "place_market_order" *.md baseline_v1_live/*.md docs/**/*.md
```

### Generate Documentation Index
```bash
# List all markdown files with line counts
find . -name "*.md" -exec wc -l {} + | sort -n
```

### Validate Links
```bash
# Check for broken internal links (manual for now)
# Future: Automated link checker script
```

---

## Future Documentation

Planned additions:
- **Performance Tuning Guide** - Optimization tips, profiling results
- **Backtest Results** - Historical performance analysis
- **Risk Management Guide** - R-multiple theory, position sizing strategies
- **Broker Integration Guide** - OpenAlgo setup, troubleshooting per broker
- **EC2 Deployment Deep Dive** - Docker, Nginx, SSL, monitoring setup

---

## Questions or Issues?

- **System behavior:** Check theory docs
- **Recent changes:** Check `docs/implementation/`
- **Deployment:** Check `DAILY_STARTUP.md` or `CLAUDE.md`
- **Code questions:** Use appropriate skill/agent (see `.claude/SUB_AGENTS_REFERENCE.md`)

For issues not covered, create a GitHub issue or document in implementation notes.
