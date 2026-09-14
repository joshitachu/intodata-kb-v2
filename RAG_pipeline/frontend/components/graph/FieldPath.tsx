import { Fragment } from "react";

/** Characters a field path may wrap after. */
const BREAK_AFTER = "/.:_>";

/**
 * A field path that WRAPS at its own separators rather than being cut off.
 *
 * Four columns in a 390px panel left roughly 110px per side, so `truncate` hid
 * the end of almost every real path: `max:PLUSPCUS...` into `to4psJson/cu...`.
 * A name you cannot read is worse than a taller row.
 *
 * The break opportunities are explicit because CSS will not break after `/` or
 * `:` on its own, and `break-all` breaks mid-word instead ("descriptio n"),
 * which is how these columns looked before. The full value stays in `title`
 * so a hover still shows it on one line.
 */
export function FieldPath({ value, className = "" }: { value: string; className?: string }) {
  if (!value) return <code className={`text-slate-400 ${className}`}>--</code>;
  const parts: string[] = [];
  let current = "";
  for (const character of value) {
    current += character;
    if (BREAK_AFTER.includes(character)) {
      parts.push(current);
      current = "";
    }
  }
  if (current) parts.push(current);
  return (
    <code className={`break-words ${className}`} title={value}>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {part}
          {index < parts.length - 1 && <wbr />}
        </Fragment>
      ))}
    </code>
  );
}
