import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MercuryCare · 售后协同工作台",
  description: "让每一次售后，都有可信的下一步。",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
