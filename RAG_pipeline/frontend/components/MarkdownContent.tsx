"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface MarkdownContentProps {
  content: string;
  className?: string;
}

function renderCitationLinks(text: string): ReactNode[] {
  // Split text on [N] patterns and make them clickable
  const parts = text.split(/(\[\d+\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (match) {
      const num = match[1];
      return (
        <a
          key={i}
          href={`#source-${num}`}
          className="text-primary hover:underline font-medium text-xs align-super"
          onClick={(e) => {
            e.preventDefault();
            document.getElementById(`source-${num}`)?.scrollIntoView({ behavior: "smooth" });
          }}
        >
          [{num}]
        </a>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={cn("prose prose-sm max-w-none dark:prose-invert", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p({ children }) {
            // Process text children to find [N] citation patterns
            const processed = processChildren(children);
            return <p>{processed}</p>;
          },
          li({ children }) {
            const processed = processChildren(children);
            return <li>{processed}</li>;
          },
          a({ href, children }) {
            return (
              <a href={href} className="text-primary hover:underline" target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            );
          },
          code({ children, className: codeClassName }) {
            const isInline = !codeClassName;
            if (isInline) {
              return (
                <code className="bg-muted px-1.5 py-0.5 rounded text-sm">
                  {children}
                </code>
              );
            }
            return (
              <code className={codeClassName}>
                {children}
              </code>
            );
          },
          pre({ children }) {
            return (
              <pre className="bg-muted rounded-lg p-4 overflow-x-auto">
                {children}
              </pre>
            );
          },
          blockquote({ children }) {
            return (
              <blockquote className="border-l-4 border-muted-foreground/30 pl-4 italic text-muted-foreground">
                {children}
              </blockquote>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function processChildren(children: ReactNode): ReactNode {
  if (!children) return children;

  if (typeof children === "string") {
    return renderCitationLinks(children);
  }

  if (Array.isArray(children)) {
    return children.map((child, i) => {
      if (typeof child === "string") {
        return <span key={i}>{renderCitationLinks(child)}</span>;
      }
      return child;
    });
  }

  return children;
}
