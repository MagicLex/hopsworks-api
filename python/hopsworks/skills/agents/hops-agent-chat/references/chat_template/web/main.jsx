// This chat app's page. It starts as Hopsworks' chat page, whole: change it as
// you like. Swap parts of it (`components`), put your own around it, or build a
// page of your own from the parts @hopsworks/agent-chat/react exports and
// CopilotKit's (@copilotkit/react-core/v2). See AGENTS.md.

import { HopsworksChat, mount } from "@hopsworks/agent-chat/react";
import "./theme.css";

mount(<HopsworksChat />);
