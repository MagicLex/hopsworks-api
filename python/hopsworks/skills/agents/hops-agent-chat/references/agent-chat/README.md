# @hopsworks/agent-chat

The chat app Hopsworks creates for an agent deployment ("Open in chat app" on the
deployment page, a checkbox when deploying an agent, or `agent.create_chat_app()`), as a
package apps are built on:

- `server/`: the chat server, `createChatServer()`, and `startChatApp()`, which builds an
  app's page and serves it with the server. Everything below under *How it works* is here,
  and stays correct whatever an app's page looks like.
- `react/`: the chat page, `<HopsworksChat />`, and the parts it is made of
  (`@hopsworks/agent-chat/react`).
- `template/`: the app an app starts as: a `server.mjs` calling `startChatApp()`, a
  `web/main.jsx` mounting `<HopsworksChat />`, a theme, and an `AGENTS.md` (also as
  `CLAUDE.md`) for whoever changes it, coding agents included.

It is installed in the `python-app-pipeline` image, in the global prefix linked at
`/node_modules`, so any folder imports it without installing it. The default chat app is
the template at `/opt/hopsworks/agent-chat-app`, its page built with the image, and chat
apps run `node /opt/hopsworks/agent-chat-app/server.mjs`.

## Customizing an app

An app's owners change it in a folder of their own and deploy that folder:

```sh
hops app chat-template support_chat      # the template, as this package's major version
# edit support_chat/ locally
hops app deploy <app> support_chat
```

The deploy writes the folder to `Resources/apps/<app>/` and records its files' hash on the
app. At each start the container checks the folder against that hash and copies it to
`/workspace/app` (`APP_DIR`, with `APP_FILES_SHA256` set); the default app then runs that
copy's `server.mjs`. Nothing is written to the project: the page is built in the pod's
temporary space, and a read-only copy of this package's source is written into the copy's
`.hopsworks/agent-chat/`, after the check. A `server.mjs` that fails to load, or a page
that fails to build, ends the start with the error in the app's log. An app with no
deployed folder runs the default app.

A chat app runs on the image's packages only (this package and what it ships): no
`npm install` in the folder.

The core stays this package's, so an app's folder gets every fix with the next image. Its
API is a contract: semver, and `CHANGELOG.md`. An app's `package.json` records the major
version it was made for (`hopsworks.agentChat`), and the app logs it when the image has
another.

## Embedding the chat in an app of its own

An app with a page of its own (a FastAPI or Flask app, say) can hold the chat instead of
being one: name the agent on the app (`agent=` in `create_app()`, or the **Agent** setting
on its page) and Hopsworks starts it as it starts a chat app, with the agent's in-cluster
address (`CHAT_AGENT_URL`) and the key its members' names are signed with
(`HOPSWORKS_APP_IDENTITY_KEY`). The app then runs the chat server beside its own, on a port
of its own, and forwards a path of its own to it:

```sh
hopsworks-agent-chat --port 8081 --base-path chat/ &
uvicorn app:app --host 0.0.0.0 --port "$APP_PORT"
```

The app's server forwards `chat/*` to `http://127.0.0.1:8081/*` as it is: method, body,
the `Cookie` header and the `Set-Cookie` headers coming back (the visitor cookie), and the
headers the Hopsworks proxy set, `X-Hopsworks-User`, `X-Hopsworks-User-Time`,
`X-Hopsworks-User-Signature` and `X-Forwarded-For`, which are how the chat server knows who
is chatting; replies are streamed (SSE), so the forward must not buffer them. Its page uses
`@hopsworks/agent-chat/react` with `apiBase="chat/"`:

```jsx
import { HopsworksChat, mount } from "@hopsworks/agent-chat/react";

mount(<HopsworksChat apiBase="chat/" />);
```

`--base-path` and `apiBase` are the same path: the server makes the links of the replies it
streams (media) with the one, the page makes its requests with the other. Nothing else
changes: who is chatting is still decided by the chat server from the signed headers, each
person's conversations are still their own, and the agent is still reached inside the
cluster only. The docs have a complete FastAPI example.

## How it works

- `react/` is the page: [CopilotKit](https://github.com/CopilotKit/CopilotKit)'s chat (MIT,
  React), built from an app's `web/main.jsx` (the default app's with the image). Agent replies are rendered as markdown
  to React elements, never as raw HTML. The agent's questions (AG-UI interrupts) are shown
  as a card in the chat (Approve/Reject for a yes/no question, a form for a schema, a text
  box otherwise) and answered with a resume, as in the chat panel. A new conversation opens
  on a welcome screen, the input in the middle with the agent's own suggested prompts
  (`suggested_prompts`, `welcome_message` and `placeholder` from its manifest) under it.
- The nav lists the person's conversations, newest first, grouped by day and named by their first
  message; it collapses to a rail of icons. The list comes from the agent's memory (its
  conversations under the person's subject and carrying their tag), and from the app's own note
  of the conversations it ran, for an agent whose memory cannot list. A conversation the runtime
  no longer holds, because the app restarted or scaled to zero, is restored from the agent's
  transcript when opened (an AG-UI `MESSAGES_SNAPSHOT`); one the agent did not remember opens
  empty.
- An element of the page marked `data-host-mark`, such as the attribution link a host that
  publishes the app adds, is moved into the nav's foot.
- Tool calls and reasoning show as quiet rows that open on their input and result. A reply lists
  as sources the records its turn's tools returned (records with `text` and a name field) and the
  links it holds: nothing is looked up for them.
- `server/agent.mjs` also keeps each run within AG-UI's ordering rules, which the client enforces by
  ending the run: two steps of one name open at once go out as `name` and `name #2`, a step
  finished twice or never started is dropped, and whatever is still open is closed before the
  run ends. Agents built on an older SDK sent such streams, and the page waited forever. A
  failure to reach the agent becomes a `RUN_ERROR` the page shows.
- `server/core.mjs` serves the page and hosts CopilotKit's Copilot Runtime at `api/copilotkit`,
  with one agent, `server/agent.mjs`: AG-UI over HTTP to the agent's in-cluster address,
  `CHAT_AGENT_URL`. No serving key reaches a browser. Only the runtime routes the page needs
  are served (`info`, and `run`, `connect` and `stop` on that agent); its thread, memory and
  debug routes are not. The agent's own memory routes are not forwarded either: the server
  only reads, for the sidebar, the list of the person's own conversations and their
  transcripts, and never erases anything. CopilotKit's telemetry is off.
- Who is chatting is the server's to say. A project member who opened the app through
  Hopsworks is named by the app proxy, signed with the app's key
  (`HOPSWORKS_APP_IDENTITY_KEY`, an HMAC-SHA256 of the username and the time, accepted for
  five minutes), and chats as their Hopsworks user: the subject the chat panel uses, so the
  agent's memory of them is the same in both. Anyone else, a public-link visitor or a
  request the signature does not cover, is anonymous: an `HttpOnly` cookie holds a random
  id, and the subject is derived from it. Every run is sent with that subject in
  `forwardedProps`, replacing whatever the page sent. Before the runtime sees a request, the
  server puts the person's tag on its conversation id (and takes it off the reply), so the
  runtime and the agent file each person's conversations apart: whatever id a page sends, it
  reaches only its own person's conversations. Ids other than letters, digits, `-` and `_`
  are refused, and opening conversations is rate-limited like running them, since opening
  one the runtime lost asks the agent for its transcript.
- An agent that scales to zero when idle is reached through the KEDA HTTP add-on's
  interceptor, which wakes it: Hopsworks sets `CHAT_AGENT_URL` (and `CHAT_AGENT_HOST`, the
  Host the interceptor routes on) at each start, from the deployment's scaling then.
- A corrected answer (an agent that streams a draft and then returns a different final text,
  `RUN_FINISHED.result.hopsworks.text`) replaces the draft on the page: the run ends with a
  `MESSAGES_SNAPSHOT` carrying it, since AG-UI has no event that takes streamed text back.
- Attachments are taken as the chat panel takes them: text files always, put into the
  message as `--- Attached file: <name> ---` blocks (by `server/agent.mjs`, so every agent can read
  them, up to 256 KB), and images, audio or any file (up to 5 MB) only when the agent's
  manifest says it takes them (`capabilities.multimodal.input`).
- Images, audio and files an agent replies with (AG-UI assistant content is text, so they come
  as `CUSTOM hopsworks.media` events) become messages linking to `./api/media/<id>`, where the
  app serves the bytes to the person they were sent to and to no one else: PNG, JPEG, GIF and
  WebP and audio inline, anything else (SVG among them) as a download. Kept in memory (256 MB,
  oldest first): a conversation restored after a restart has its text but not its media.
- A conversation restored from the agent's transcript can be rated on its latest reply, which
  the agent resolves by conversation; an older restored reply has no thumbs, since the
  transcript does not say which turn's trace it was.
- Thumbs up records at once. Thumbs down opens the chat panel's form under the reply: a
  category (wrong answer, missing info, wrong tool use, refused, unsafe, other), the answer
  it should have given, and a note; it is recorded when saved, as `issue_category`,
  `corrected_answer` and `note`. Both are forwarded with `source: "chat-app"`, so a reviewer
  can tell them from the chat panel's. They name the reply; the server knows which trace it belongs
  to (or, for an untraced agent, which conversation), and only its own person can rate it.

## Parity with Hopsable's chat

The page follows the chat of Hopsable (the Hopsable repository, `apps/web`). A change to one of
these parts there or here is made on both sides:

| Here | Hopsable |
| --- | --- |
| frame, panel, nav, rail | `components/shell.tsx`, `.claude/rules/ui.md` (nesting) |
| `Step`, `ToolCalls`, `Reasoning` | `components/workspace/chat.tsx` (`Row`, `ToolStep`, `ThinkingStep`) |
| `Waiting` | `components/ui/wait-line.tsx` |
| the user message tint, the reply's markdown | `components/workspace/chat.tsx` (`UserMessage`), `markdown-view.tsx` |

## Settings

Environment variables of the app, set by Hopsworks and editable on the app afterwards:

| Variable | Meaning |
| --- | --- |
| `CHAT_AGENT_URL`, `CHAT_AGENT_HOST` | Where the agent is reached, and the Host header used, through the KEDA interceptor for an agent that scales to zero; set by Hopsworks at each start |
| `CHAT_AGENT_NAME`, `CHAT_AGENT_DESCRIPTION` | Shown in the header |
| `CHAT_PROJECT_ROOT` | Where the project's files are mounted, set by Hopsworks; the theme folder is inside it |
| `HOPSWORKS_APP_IDENTITY_KEY` | The key members' names are signed with, set by Hopsworks |
| `CHAT_TITLE` | The page title; the agent's name from its manifest when unset |
| `CHAT_WELCOME` | The welcome screen's heading; the agent's `welcome_message`, else none |
| `CHAT_SHOW_TOOL_CALLS` | `false` hides tool calls and reasoning from the page (they still reach the browser) |
| `CHAT_ACCENT_COLOR` | The page's accent: `#rgb`, `#rrggbb`, `rgb(...)` or `hsl(...)` |
| `CHAT_LOGO_URL` | An `https://` image for the header, when the theme has no logo |
| `CHAT_THEME_DIR` | A folder of the project, such as `Resources/chat-theme`, holding `theme.css` (loaded after the page's own styles) and a logo (`logo.svg`, `logo.png`, `logo.jpg` or `logo.webp`) |
| `CHAT_MAX_RUNS_PER_MINUTE` | Per visitor; default 20 |
| `CHAT_MAX_BODY_BYTES` | Largest request, attachments included; default 10 MB |

Theme files are served with a policy that forbids script, so an SVG logo opened on its
own cannot run any; the theme folder must be inside the project's mount.

## Building and testing

The image installs the package in its global prefix and runs `npm install` there, as a
project of its own so its `overrides` apply (npm ignores a dependency's): `zod`, `undici`,
`@fastify/busboy`, `katex`, `@hono/node-server` and `graphql-scalars` are lifted past their advisories, and `npm audit --omit=dev`
should stay at zero. `streamdown` is pinned for `patches/`, which patch-package applies on
install. No lockfile is kept in the repo: `package.json` pins the direct
dependencies. The image then runs the tests and builds the default app's page.

```bash
SCARF_ANALYTICS=false npm install
npm test        # the default app, a deployed app run from APP_DIR, the markdown
```

To run the default app locally against an agent, from a folder where
`node_modules/@hopsworks/agent-chat` is this package:

```bash
cp -r template app && cd app
CHAT_AGENT_URL=http://localhost:8000 PORT=8080 node server.mjs
```
