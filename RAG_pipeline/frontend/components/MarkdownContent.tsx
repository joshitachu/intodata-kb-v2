"use client";

import { useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";
import { Check, Copy } from "lucide-react";

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
    <div className={cn("prose prose-sm prose-neutral dark:prose-invert max-w-none", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre({ children }) {
            return <>{children}</>;
          },
          code({ className, children }) {
            const raw = String(children ?? "").replace(/\n$/, "");
            const match = /language-(\w+)/.exec(className ?? "");
            const language = match?.[1] ?? "";
            const isBlock = raw.includes("\n") || Boolean(language);
            if (!isBlock) {
              return <code className={className}>{children}</code>;
            }
            if (language === "mermaid") {
              return <MermaidDiagram definition={raw} />;
            }
            return <CodeBlock code={raw} language={language} />;
          },
          p({ children }) {
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
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

type MermaidState = "rendered" | "unparsed" | "failed";

// mermaid.render() appends its working element to document.body when no container is
// passed, and removes it again only on the paths it treats as clean. Whatever it leaves
// behind stays in the DOM for the life of the page -- that is the row of "Syntax error
// in text" bombs below the app. Remove this instance's nodes ourselves, by their
// documented ids ("d"/"i" prefixes, mermaid.core.mjs render()).
//
// Only body-level nodes: the SVG mermaid RETURNS carries `diagramId` as well, so an
// unscoped lookup deletes the diagram we just inserted into our own container.
function removeMermaidWorkingNodes(diagramId: string) {
  for (const id of [diagramId, `d${diagramId}`, `i${diagramId}`]) {
    const node = document.getElementById(id);
    if (node?.parentElement === document.body) node.remove();
  }
}

function MermaidDiagram({ definition }: { definition: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const reactId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const [state, setState] = useState<MermaidState>("unparsed");

  useEffect(() => {
    let cancelled = false;
    const diagramId = `nexus-mermaid-${reactId}`;

    async function renderDiagram() {
      try {
        const { default: mermaid } = await import("mermaid");
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          // Never let mermaid draw its own error diagram: it draws into the body-level
          // working element and then throws, which is how the orphans were created.
          suppressErrorRendering: true,
          theme: window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default",
          flowchart: { htmlLabels: false, curve: "basis" },
        });
        // Validate before rendering. parse({ suppressErrors }) touches no DOM, so a
        // definition that is still streaming in cannot reach the leaking code path.
        const parsed = await mermaid.parse(definition, { suppressErrors: true });
        if (cancelled) return;
        if (!parsed) {
          setState("unparsed");
          return;
        }
        const { svg, bindFunctions } = await mermaid.render(diagramId, definition);
        if (cancelled || !containerRef.current) return;
        containerRef.current.innerHTML = svg;
        bindFunctions?.(containerRef.current);
        setState("rendered");
      } catch {
        if (!cancelled) setState("failed");
      } finally {
        removeMermaidWorkingNodes(diagramId);
      }
    }

    // While an answer streams, `definition` grows token by token and is invalid most of
    // the way. Debounce so a diagram is parsed once it settles, not once per chunk.
    const timer = window.setTimeout(() => void renderDiagram(), 150);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      // The rendered SVG is deliberately left in place: a re-run replaces it, and
      // blanking it here would flash an empty frame on every streamed chunk.
      removeMermaidWorkingNodes(diagramId);
    };
  }, [definition, reactId]);

  const rendered = state === "rendered";

  // The container stays mounted in every state: the effect writes the SVG into it
  // before flipping the state, so a conditionally mounted ref would always be null.
  return (
    <>
      <div
        ref={containerRef}
        className={cn(
          "not-prose my-5 w-full justify-center overflow-x-auto rounded-xl border bg-white p-4 dark:bg-slate-950 [&_svg]:h-auto [&_svg]:max-w-full",
          rendered ? "flex min-h-24" : "hidden",
        )}
        role="img"
        aria-label="Documentation flow diagram"
        aria-hidden={!rendered}
      />
      {!rendered && (
        <div className="not-prose my-4">
          {state === "failed" && (
            <p className="mb-2 text-xs text-amber-700 dark:text-amber-300">
              Diagram rendering failed; showing its Mermaid source.
            </p>
          )}
          <CodeBlock code={definition} language="mermaid" />
        </div>
      )}
    </>
  );
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);
  const lang = language || "text";

  function handleCopy() {
    void navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="not-prose my-3 overflow-hidden rounded-lg border bg-slate-950 text-slate-100">
      <div className="flex items-center justify-between border-b border-white/10 bg-white/[0.04] px-3 py-1.5">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
          {lang}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[11px] text-slate-300 hover:bg-white/10 hover:text-white"
          title="Copy code"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="m-0 max-w-full overflow-x-auto p-3 text-[13px] leading-relaxed">
        <code>{highlightCode(code, lang)}</code>
      </pre>
    </div>
  );
}

function highlightCode(code: string, language: string): ReactNode[] {
  const keywordPattern = keywordRegexFor(language);
  if (!keywordPattern) return [code];

  const tokenPattern = new RegExp(
    `(${keywordPattern.source}|"(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\\b\\d+(?:\\.\\d+)?\\b|\\/\\/.*|#.*|--.*)`,
    "g",
  );
  const parts = code.split(tokenPattern);
  return parts.map((part, index) => {
    if (!part) return null;
    if (keywordPattern.test(part)) {
      keywordPattern.lastIndex = 0;
      return <span key={index} className="text-sky-300">{part}</span>;
    }
    keywordPattern.lastIndex = 0;
    if (/^["']/.test(part)) {
      return <span key={index} className="text-emerald-300">{part}</span>;
    }
    if (/^\d/.test(part)) {
      return <span key={index} className="text-amber-300">{part}</span>;
    }
    if (/^(\/\/|#|--)/.test(part)) {
      return <span key={index} className="text-slate-500">{part}</span>;
    }
    return part;
  });
}

function keywordRegexFor(language: string): RegExp | null {
  const normalized = language.toLowerCase();
  if (["ts", "tsx", "js", "jsx", "javascript", "typescript"].includes(normalized)) {
    return /\b(?:async|await|const|let|var|function|return|if|else|for|while|import|from|export|type|interface|class|extends|new|try|catch|throw|null|undefined|true|false)\b/g;
  }
  if (["py", "python"].includes(normalized)) {
    return /\b(?:async|await|def|class|return|if|elif|else|for|while|import|from|as|try|except|raise|with|lambda|None|True|False)\b/g;
  }
  if (["sql"].includes(normalized)) {
    return /\b(?:select|from|where|join|left|right|inner|outer|insert|update|delete|create|alter|drop|table|view|procedure|with|as|and|or|group|order|by|having|limit|case|when|then|else|end)\b/gi;
  }
  return null;
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
