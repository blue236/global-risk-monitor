# Weekly TODO — Global Risk Monitor (GRM)

## P1 (Must-do)
1. Stabilize Telegram commands
   - Validate operation of `/status`, `/refresh`, `/report`, `/triggers`, `/help`
   - Remove scheduler `max_instances` warnings (prevent overlapping execution)

2. Stabilize external access + login
   - Finalize operations guide for `GRM_COOKIE_SECURE` by HTTP/HTTPS mode
   - Re-verify login failure/block policy behavior

3. Operational safeguards
   - Add lock handling for duplicate `/refresh` calls (block repeated call while running)

## P2
4. Alert noise management
   - Review repeated-message suppression policy (hash/time window)

5. Documentation updates
   - Add Telegram command usage to README
   - Add HTTPS migration checklist

---

# Day Plan (Today)
1. Reproduce `max_instances` warning and lock down root cause
2. Patch to prevent duplicate execution of Telegram poll job
3. Add lock handling to `/refresh`
4. Document .env operations guide (HTTP/HTTPS)
5. Run Telegram command smoke tests after restart
