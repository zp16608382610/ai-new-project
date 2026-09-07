import type { Metadata } from "next";

import ConsoleClient from "./ConsoleClient";

export const metadata: Metadata = {
  title: "Console",
};

export default function ConsolePage() {
  return <ConsoleClient />;
}
