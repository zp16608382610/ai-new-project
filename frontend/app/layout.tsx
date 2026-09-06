import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "AI Customer Service Agent",
    template: "%s | AI Customer Service Agent",
  },
  description: "Enterprise AI Customer Service Agent - 项目初始化阶段",
};

const navItems = [
  { href: "/", label: "首页" },
  { href: "/chat", label: "Chat" },
  { href: "/console", label: "Console" },
  { href: "/evaluation", label: "Evaluation" },
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="site-header">
          <nav className="site-nav">
            {navItems.map((item) => (
              <Link key={item.href} href={item.href} className="nav-link">
                {item.label}
              </Link>
            ))}
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}