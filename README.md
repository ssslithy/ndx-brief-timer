# NDX brief timer

Starts the NDX Morning Brief at 08:52 New York on each trading day.

GitHub starts this account's scheduled jobs hours late, or skips them. So this repo's job is scheduled through the night. The first run to start waits until 08:52 New York, then starts the brief.

This repo is public so that the waiting is free. It contains only this timer. The token it uses is stored as a GitHub secret (`BRIEF_TOKEN`) and is never shown. The token can only start and list runs of the brief's own repository.

The source is kept in the brief's repository under `timer/`, with tests.
