<!-- BEGIN reel-agent reminders -->
## Reminders and to-dos (shared by every session)

There is one reminder / to-do list for all sessions on this PC: `{{REMIND}}`. It survives any session closing: each reminder is booked in Windows Task Scheduler for its exact time and sent to my Telegram then (no polling).

- When I say "remind me…", "don't let me forget…", "add to my to-do list", or ask you to remember something for later that needs doing: add it there, don't just keep it in this conversation.
  `python {{REMIND_FWD}} add "<what, specific enough to act on alone>" [--at "YYYY-MM-DD HH:MM" | --in 2h] [--every day|weekday|week|month] --source "<project or folder>" [--note "<paths, commands, context I'll need>"]`
  No time = plain to-do (no ping). Turn relative times ("tomorrow morning", "in 2 hours") into a concrete time; "morning" = 09:00. Reply with the ID and the time.
- "What's on my list?" → `remind.py list`. Done / snooze / delete → `remind.py done R3`, `snooze R3 1h`, `delete R3`.
- At the start of a session, if I ask what's pending or you need my priorities, check `remind.py due`.
<!-- END reel-agent reminders -->
