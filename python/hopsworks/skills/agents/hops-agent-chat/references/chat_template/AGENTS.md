# This chat app

A chat page for a Hopsworks agent deployment, run as a Hopsworks app. People
open it to talk to the agent: project members signed in as themselves, and,
when the app's public link is on, anyone holding the link, anonymously.

It is built on `@hopsworks/agent-chat`, which is installed in the app's image
(`python-app-pipeline`). The package is the chat's core; this folder is what
this app makes of it, and is yours to change.

## Files

| File | What it is |
| --- | --- |
| `server.mjs` | The app's server: `startChatApp()` with this app's options and routes |
| `web/main.jsx` | The page (React 19). Starts as `<HopsworksChat />`, the default page, whole |
| `web/theme.css` | The page's look, loaded after its own styles |
| `web/index.html` | Optional: the page's HTML, if the default one does not do (it must load `app.css` and `app.js` and have a `#root`) |
| `web/static/` | Optional: images and other files, served at `static/…` |
| `package.json` | `hopsworks.agentChat` is the package's major version this app was made for |
| `.hopsworks/agent-chat/` | **Read only.** The package's source, as the app runs on it: written into the app's running copy at every start; the deploy skips it. Read it; do not edit it |

## How it runs

Edit this folder locally, then publish it:

```sh
hops app deploy <this app's name> <this folder>
```

The deploy writes the folder to `Resources/apps/<app>/` and records its files'
hash. At each start the app checks its files against that hash, runs a copy of
them, builds the page from `web/` (esbuild, React and CopilotKit come from the
package), and serves it with the chat server. A file changed in
`Resources/apps/<app>/` after the deploy fails the next start: deploy again. A
start that fails to load `server.mjs`, or to build the page, ends with the error
in the app's log; read it with `hops app logs <app>`.

A new folder comes from `hops app chat-template <dir>`. Nothing in this folder
runs in the terminal: the package is in the app's image, not the terminal's.

## What to change, and where

**The look**: `web/theme.css`. The page's colours and spacing are CSS
variables (`--hw-accent`, `--hw-accent-soft`, `--hw-bg`, `--hw-surface`,
`--hw-text`, `--hw-muted`, `--hw-border`, `--hw-radius`; CopilotKit's chat
takes its colours from them); every element of the page has an `hw-` class. The accent
colour has a setting too (`accentColor` in `server.mjs`, or the app's
`CHAT_ACCENT_COLOR` variable): when set, the page applies it inline, over
`theme.css`, so set the accent there rather than in the CSS, or make both agree.

**The title** is the `title` setting (else `CHAT_TITLE`, else the agent's
name from its manifest). The page shows it in the header and as the browser tab's title.

**Text on the page** says what is: no greeting filler, no line explaining what the page
shows. A text block takes its column's width: no `max-width` or `ch` cap on a paragraph or
heading, no `text-wrap: balance`.

**The page**: `web/main.jsx`. `<HopsworksChat />` takes

- `headerStart`, `headerEnd`: shown in its header, before the title and next to
  who is signed in (a brand mark, a badge, a link);
- `footer`: shown under the chat; `children`: shown above it (both in the chat's
  column, at their own height);
- `components`: parts to use instead of its own: `Header` (`{config,
  onToggleList, start, end}`), `Sidebar` (`{open, conversations, threadId,
  onOpen, onNew, onClose}`, or `null` for none), `AssistantMessage` (wraps
  CopilotKit's; keep the thumbs by wrapping the exported one). Prefer the slots
  above to a header of your own: one has to keep the phone's menu button
  (`onToggleList`), the logo (resolve it with `appUrl()`) and the rest; start
  from `Header` in `.hopsworks/agent-chat/react/index.jsx` if you do;
- `chatProps`: more props for CopilotKit's `<CopilotChat />` (labels, input, …).

Inside it, `useChat()` gives `config` (what the server's `/api/config` says:
`title`, `welcome`, `prompts`, `agent` {name, description, running}, `you`
{member, name} for who is signed in, `theme`), `threadId`, `conversations`,
`openConversation(id)`, `newConversation()` and `refreshConversations()`. Many
"is this a member?" needs are `config.you.member`, with no route of your own.

The parts it is made of are exported too, for a page of your own: `Header`,
`Sidebar`, `Questions` (the agent's questions, as cards), `QuestionCard`,
`AssistantMessage` (a reply with the thumbs), `NegativeFeedback` (the
thumbs-down form), `sendFeedback()`, `appUrl()` (a URL of this app, wherever the
proxy serves it), `failureText()` and `mount()`. CopilotKit's own components
and hooks are in `@copilotkit/react-core/v2`.

**The server**: `server.mjs`. `startChatApp()` takes settings that win over the
app's environment variables (`title`, `welcome`, `agentName`,
`agentDescription`, `accentColor`, `logoUrl`, `themeDir`, `showToolCalls`,
`maxRunsPerMinute`, `maxBodyBytes`) and `routes`, a function for routes of this
app's own: `routes(req, res, { path, who, sendJson, readJson })`, asked after the
chat's own routes. It answers with `sendJson(status, body)` (or on `res`) and
returns `true` when it answered, `false` to let the request go on.

- `path` is the path within the app (`/api/status`): the Hopsworks proxy's
  prefix is already off. Keep the app's own routes under `/api/`.
- `who` is who the chat server says is asking: `{ member, name, subject }`, with
  `member` true and `name` their Hopsworks user for a signed-in member, `member`
  false and `name` null for anyone else, and `subject` what the agent knows them by.
- `readJson()` reads the request's JSON body (up to `maxBodyBytes`).

The page reaches a route as `fetch(appUrl("api/status"))`: the app is served
under a path of the proxy's choosing, so `fetch("/api/status")` would miss it.
The same goes for files in `web/static/`: `appUrl("static/logo.svg")`.

**Packages**: the app uses the image's packages only: this package and what it
ships (React, CopilotKit, esbuild). No `npm install`: the deploy skips
`node_modules`, and refuses a `package.json` that lists other dependencies.

## Keep it safe

The page may be public. These are the package's, and stay correct only as
long as this app leaves them to it:

- **Who is chatting** is decided by the chat server, from a signature only
  Hopsworks can make, or an anonymous cookie. In a route of your own, use
  `who` from the context; never a header, cookie or field of the request.
- **Conversations are per person**: the server keeps each person's apart. Do
  not add routes that read the agent's memory or conversations for someone
  else.
- **No keys in the page**: the server reaches the agent inside the cluster;
  nothing the browser receives can call it.
- **What the agent says is shown through CopilotKit's markdown rendering**,
  never as HTML: do not use `dangerouslySetInnerHTML` (or the like) on
  anything that came from the agent or a visitor.

## Going back, and upgrading

- Start over from the default: `hops app chat-template <new dir>`, then deploy
  it with `hops app deploy <app> <new dir>`.
- A newer image may bring a newer package: `.hopsworks/agent-chat/CHANGELOG.md`
  says what changed, and `.hopsworks/agent-chat/template/` is the default app
  as it is now, to compare with.
