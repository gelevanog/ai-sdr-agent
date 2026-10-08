"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/accounts", label: "Accounts" },
  { href: "/review", label: "Approval queue" },
  { href: "/sequences", label: "Sequences" },
  { href: "/replies", label: "Replies" },
  { href: "/evaluation", label: "Evaluation" },
  { href: "/audit", label: "Audit log" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const path = usePathname();
  return (
    <header className="bg-forest-deep text-white">
      <div className="mx-auto flex max-w-[1380px] items-center gap-8 px-6">
        <Link href="/" className="flex items-center gap-2 py-3">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
            <circle cx="14" cy="14" r="9" fill="none" stroke="#9fd3b6" strokeWidth="3" />
            <path d="M20.5 20.5 L28 28" stroke="#9fd3b6" strokeWidth="3.5" strokeLinecap="round" />
            <path d="M10 14 l3 3 l5 -6" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="font-serif text-xl font-semibold tracking-tight">Scout</span>
          <span className="ml-1 hidden rounded bg-white/10 px-1.5 py-0.5 text-[11px] text-emerald-100 md:inline">AI SDR · human approved</span>
        </Link>
        <nav className="flex flex-1 flex-wrap gap-1 text-sm">
          {LINKS.map((link) => {
            const active = link.href === "/" ? path === "/" : path.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-t-md px-3 pt-4 pb-3 transition ${active ? "bg-paper text-forest-deep font-medium" : "text-emerald-50/80 hover:text-white"}`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
