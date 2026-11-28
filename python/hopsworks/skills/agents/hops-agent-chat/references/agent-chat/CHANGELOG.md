# Changelog

The API of `@hopsworks/agent-chat`: what `server.mjs` and `web/` of an app may use. A major
version changes it in a way an app may have to follow; a minor one adds to it.

## 2.0.0

A customized chat app runs only the files its deploy wrote.

- With `APP_FILES_SHA256` set (an app deployed with `hops app deploy`), `startChatApp()` runs
  `server.mjs` from `APP_DIR`, the copy the container checked against that hash, and writes
  `.hopsworks/agent-chat/` into that copy. Nothing is written to the project. With the hash set
  and no `APP_DIR`, the start fails.
- `CHAT_APP_DIR` is no longer read, and nothing is copied into a project folder. `initApp()` and
  `customAppDir()` are removed.
- A deployed `server.mjs` that fails to load, or a page that fails to build, ends the process
  non-zero with the error in the log. The default page is no longer served in its place.
- A chat app uses the image's packages only: the template's docs drop `npm install`.
- `@hono/node-server` 2.1.11 in `overrides`, past GHSA-qv5m-9pm6-hqp3 and GHSA-jpvv-r467-5p4w
  (2.0.0 to 2.1.5).

## 1.2.1

- No greeting by default: without `CHAT_WELCOME` or the agent's `welcome_message`, a new
  conversation shows the input and the prompts with no heading (was "How can I help you today?").
- `template/AGENTS.md` carries the text rule for whoever changes a page: text says what is, a text
  block takes its column's width.

## 1.2.0

The chat page in the Hopsworks look, with the parts of Hopsable's chat.

- Layout: the page is the light gray `--hw-surface` frame; the conversation is one white panel with
  a border; the nav sits on the frame. The header is one row on the panel: the agent's name, then
  the conversation's title (the agent's description in a new one), and an initial for who is signed
  in. A logo from the theme still shows.
- The nav collapses to a rail of icons at every width (its toggle, kept per browser); under 760 px
  it opens over the chat and closes on Escape, a pick or a click outside. Conversations are grouped
  Today, Previous 7 days and Older; the active one is a white row with a border.
- Host-mark slot: an element of the page marked `data-host-mark` (a host's attribution link) is
  moved into the nav's foot, its text as `title` and `aria-label`; the rail shows its icon only.
- A new conversation opens on the welcome line, the input and the agent's prompts as a list of
  rows. Text blocks take their column's width; CopilotKit's disclaimer under the input is off (Lex, 2026-11-12: text says what is, nothing the screen already makes plain).
- Shorter page copy: "Feedback recorded.", "<agent> is stopped.", "Expired.".
- Tool calls are quiet rows (icon, name, the call's subject, the number of results, a spinner while
  it runs) that open on the call's input and result, highlighted, each with copy and download.
  Reasoning is one such row. `CHAT_SHOW_TOOL_CALLS=false` still hides both.
- Sources: when a turn's tools returned records with `text` and a name field (`title`, `file`,
  `file_name`, `source`, `doc_id`, `url` or `id`), as a search tool does, the reply lists them as
  chips under its text; a chip opens the passages it came from. Links in the reply are listed too.
- Three dots show before the first token.
- New exports: `Icon`, `ToolCalls`, `Reasoning`, `Waiting`, `Welcome`, `Sources`, `sourcesOf()`.
  `Sidebar` takes `collapsed` and `onToggle`; `Header` takes `conversationTitle`. Everything from
  1.1 is kept. New variables: `--hw-hover`, `--hw-danger`, `--hw-control-radius`, `--hw-nav`,
  `--hw-rail`.

## 1.1.4

- Surfaces alternate as in the Hopsworks and Hopsable UIs: the page and the conversation list are
  the light gray `--hw-surface`; the input, suggestions, question and feedback cards are white with
  a border, the active conversation a white row; a user message is tinted with the accent, and a
  table head, code header and inline code take the darker `--hw-recessed`. Header and list have no
  divider lines. Inline code no longer shows backticks. New variable: `--hw-recessed`.
- A built page's stamp no longer depends on how the app's path is spelled: the same app reached
  through a symlink (macOS `/var` is `/private/var`) reuses its build instead of building again.

## 1.1.3

- Table cells have padding (8px by 16px) and the first column is no longer flush: CopilotKit's
  prose rules overrode its own cell padding; the page's styles set it.

## 1.1.2

- Tables in the chat's replies render as tables. Streamdown 1.6.11, CopilotKit's markdown
  renderer, put its wrapper's name on the `<table>`, which CopilotKit's CSS lays out as a flex
  column; `patches/streamdown+1.6.11.patch` (patch-package, run on install) removes it.
  Streamdown is pinned to 1.6.11 in `overrides`, so the version and the patch move together.
- `@hono/node-server` 2.1.5 and `graphql-scalars` 2.0.1 in `overrides`, past GHSA-rmxm-3fg6-px4f
  and GHSA-x8rm-f576-gq67.

## 1.1.1

- The page has the Hopsworks app look (the hops-app skeleton's tokens): CopilotKit's chat takes
  its colours, radius and accent from the `--hw-` variables, `CHAT_ACCENT_COLOR` included; the
  input has one frame and its buttons none. New variables: `--hw-accent-soft`, `--hw-radius`.
- The page is light only, as Hopsworks apps are: the dark colours are gone.
- The title falls back to the agent's name from its manifest, then to `CHAT_AGENT_NAME`.

## 1.1.0

The chat in an app of its own. An app that names an agent (its `agent` setting) is started
with the agent's address and an identity key, like a chat app, and can run the chat server
beside its own server, behind a path of its own.

- `hopsworks-agent-chat [--port 8081] [--base-path chat/]`: the chat server alone, no page.
- `createChatServer({ basePath })`, or `CHAT_BASE_PATH`: where the page reaches the server,
  relative to the page; the links the chat makes (media) start with it.
- `@hopsworks/agent-chat/react`: `<HopsworksChat apiBase="chat/" />` and `setApiBase()`, the
  same for the page; `appUrl()` follows it. `stopOnSignal(server)` is exported.

## 1.0.0

The chat app as a package.

- `startChatApp({ appDir, ...options })`: builds the app's `web/main.jsx` when it changed and
  serves it. Options win over the environment: `title`, `welcome`, `agentName`,
  `agentDescription`, `accentColor`, `logoUrl`, `themeDir`, `showToolCalls`,
  `maxRunsPerMinute`, `maxBodyBytes`, `port`, and `routes(req, res, { path, who, sendJson,
  readJson })` for the app's own routes.
- `createChatServer(options)`, `buildPage(appDir, outDir)`, `pageFor(appDir)`,
  `initApp(appDir)`, `customAppDir(env)`.
- `@hopsworks/agent-chat/react`: `HopsworksChat` (`headerStart`, `headerEnd`, `footer`,
  `children`; `components`: `Header`, `Sidebar`, `AssistantMessage`; `chatProps`), `useChat()`, `mount()`, `Header`, `Sidebar`,
  `Questions`, `QuestionCard`, `AssistantMessage`, `NegativeFeedback`, `sendFeedback()`,
  `appUrl()`, `failureText()`.
- `CHAT_APP_DIR`: run the app from a folder of the project, copied from the template on
  its first start.
