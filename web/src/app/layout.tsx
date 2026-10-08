import type { Metadata } from "next";

import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "Scout - AI SDR with citations and human approval",
  description: "Researches accounts with citations, qualifies them against your ICP and drafts grounded outreach that waits for your approval.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">
        <Nav />
        <main>{children}</main>
      </body>
    </html>
  );
}
