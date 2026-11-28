// This chat app's server: Hopsworks' chat server (@hopsworks/agent-chat, from the
// app's image) serving the page in ./web. Who is chatting, which conversations
// are whose, and how the agent is reached stay the package's, so they are kept
// up to date by the image. What this app adds goes in the options below.
// See AGENTS.md.

import { startChatApp } from "@hopsworks/agent-chat";

await startChatApp({
  appDir: import.meta.dirname,
  // Settings that win over the app's environment variables, for example:
  // title: "Support",
  // welcome: "Ask us anything about your order",
  // accentColor: "#5b4cff",
  //
  // Routes of the app's own, asked after the chat's: answer, and return true.
  // `who` is who the chat server says is asking; never trust the request for it.
  // routes: async (req, res, { path, who, sendJson }) => {
  //   if (req.method === "GET" && path === "/api/hello") {
  //     sendJson(200, { hello: who.name ?? "visitor" });
  //     return true;
  //   }
  //   return false;
  // },
});
