---
description: Hopsworks agent piece. Creates, edits or deletes an agent deployment with its chat app through hops-agent-builder.
argument-hint: "[instruction]"
model: haiku
---

You are running `/hops-piece-agent` with arguments: `$ARGUMENTS`, reached from `/hops`. The rules, the pieces and the words of `/hops` hold here.

## Agent

1. `hops agent list`. An instruction naming an existing agent is an edit; "delete" is a delete;
   otherwise a new agent. With several candidates, ask which.
2. **Create.** The description is the instruction; without one, ask what the agent should answer
   and for whom. What it reads: the feature groups the request names, else the ones this
   conversation made or explored; with none, the table piece runs first. Ask the name, proposing a
   slug from the description (a letter first, then letters and digits).
3. **The LLM.** `anthropic/claude-sonnet-5-5` unless the user names another model. Its API key is
   a secret: ask for it as a credential (never in a message or with AskUserQuestion), saved as
   `<provider>_api_key`; `hops secrets list` shows whether one is already saved under that name,
   and one that is saved is used without asking.
4. **Edit.** Ask what should change, in words, unless the instruction says it.
5. **Delete.** Ask the user to confirm the exact name. Its chat app and uploaded code go with it.
6. Spawn **hops-agent-builder** with the Agent tool and this prompt, then print what it returns:

   ```
   action: create | edit | delete
   name: <name>
   description: <the user's words>
   change: <for an edit, the user's words>
   reads: <feature groups, name:version, one per line>
   model: <provider>/<model>
   key_secret: <the secret's name>
   where: <Hopsworks terminal | external client>
   ```
