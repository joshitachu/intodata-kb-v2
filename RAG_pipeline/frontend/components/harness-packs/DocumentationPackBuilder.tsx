"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { CreateDocumentationPackRequest } from "@/types";

interface DraftDocument {
  name: string;
  sections: string;
  instructions: string;
}

interface DocumentationPackBuilderProps {
  onCreate: (draft: CreateDocumentationPackRequest) => Promise<void>;
}

const EMPTY_DOCUMENT: DraftDocument = {
  name: "EXECUTIVE_SUMMARY",
  sections: "Overview\nArchitecture\nOperations\nEvidence Register",
  instructions: "",
};

export function DocumentationPackBuilder({ onCreate }: DocumentationPackBuilderProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [documents, setDocuments] = useState<DraftDocument[]>([{ ...EMPTY_DOCUMENT }]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function updateDocument(index: number, patch: Partial<DraftDocument>) {
    setDocuments((current) => current.map((document, position) => (
      position === index ? { ...document, ...patch } : document
    )));
  }

  async function create() {
    const normalizedDocuments = documents.map((document) => ({
      name: document.name.trim(),
      sections: document.sections.split("\n").map((section) => section.trim()).filter(Boolean),
      instructions: document.instructions.trim() || null,
    }));
    if (!name.trim() || normalizedDocuments.some((document) => !document.name)) {
      setError("Enter a Pack name and a name for every document.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onCreate({
        name: name.trim(),
        description: description.trim() || null,
        documents: normalizedDocuments,
      });
      setName("");
      setDescription("");
      setDocuments([{ ...EMPTY_DOCUMENT }]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Documentation Pack could not be created.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-label="Create documentation Pack" className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm font-medium">
          Pack name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Operations handbook"
            className="mt-2 h-10 w-full border border-input bg-background px-3 text-sm"
          />
        </label>
        <label className="text-sm font-medium">
          Description
          <input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Documentation for operating this project"
            className="mt-2 h-10 w-full border border-input bg-background px-3 text-sm"
          />
        </label>
      </div>

      <div className="space-y-4">
        {documents.map((document, index) => (
          <div key={index} className="border-t border-border pt-4 first:border-t-0 first:pt-0">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Document {index + 1}</h3>
              {documents.length > 1 && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-8 px-0"
                  onClick={() => setDocuments((current) => current.filter((_, position) => position !== index))}
                  aria-label={`Remove document ${index + 1}`}
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                </Button>
              )}
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-medium text-muted-foreground">
                Document name
                <input
                  value={document.name}
                  onChange={(event) => updateDocument(index, { name: event.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, "_") })}
                  className="mt-1 h-9 w-full border border-input bg-background px-3 font-mono text-sm text-foreground"
                />
              </label>
              <label className="text-xs font-medium text-muted-foreground">
                Required sections, one per line
                <textarea
                  value={document.sections}
                  onChange={(event) => updateDocument(index, { sections: event.target.value })}
                  className="mt-1 min-h-24 w-full resize-y border border-input bg-background p-3 text-sm text-foreground"
                />
              </label>
            </div>
            <label className="mt-3 block text-xs font-medium text-muted-foreground">
              Writing instructions
              <textarea
                value={document.instructions}
                onChange={(event) => updateDocument(index, { instructions: event.target.value })}
                placeholder="Optional instructions for evidence, tone, standards, and expected depth"
                className="mt-1 min-h-24 w-full resize-y border border-input bg-background p-3 text-sm text-foreground"
              />
            </label>
          </div>
        ))}
      </div>

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap justify-between gap-3">
        <Button
          variant="secondary"
          onClick={() => setDocuments((current) => [...current, { name: `DOCUMENT_${current.length + 1}`, sections: "", instructions: "" }])}
        >
          <Plus className="h-4 w-4" aria-hidden />
          Add document
        </Button>
        <Button onClick={create} isLoading={saving}>Save reusable Pack</Button>
      </div>
    </section>
  );
}
