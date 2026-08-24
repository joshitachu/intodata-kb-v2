"use client";

import {
  useState,
  useRef,
  useEffect,
  useCallback,
  type FormEvent,
  type KeyboardEvent,
  type ChangeEvent,
  type ReactNode,
} from "react";
import { ArrowUp, FileArchive, ImagePlus, Loader2, Square, X } from "lucide-react";
import { uploadZip, type UploadedZipReceipt } from "@/lib/mcpClient";
import { cn } from "@/lib/utils";
import {
  SlashCommandPalette,
  useSlashCommandPalette,
} from "@/components/SlashCommandPalette";
import type { SlashCommand } from "@/types";

const MAX_IMAGES = 4;
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];
const IMAGE_ONLY_PROMPT = "Analyze the attached image.";
const MAX_ZIP_SIZE = 500 * 1024 * 1024;

export interface ZipUploadTarget {
  projectId: string;
  platform: string;
}

interface ChatInputProps {
  onSend: (message: string, images?: string[]) => void;
  isLoading: boolean;
  disabled?: boolean;
  placeholder?: string;
  toolbarContent?: ReactNode;
  hideImagePicker?: boolean;
  zipUploadTarget?: ZipUploadTarget;
  onZipUploaded?: (receipt: UploadedZipReceipt) => void | Promise<void>;
  slashCommands?: SlashCommand[];
  onSlashCommand?: (name: string, argumentsText: string) => boolean;
}

export function ChatInput({
  onSend,
  isLoading,
  disabled,
  placeholder,
  toolbarContent,
  hideImagePicker,
  zipUploadTarget,
  onZipUploaded,
  slashCommands = [],
  onSlashCommand,
}: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [zipProgress, setZipProgress] = useState<number | null>(null);
  const [zipError, setZipError] = useState<string | null>(null);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const zipAbortRef = useRef<AbortController | null>(null);
  const pendingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);

  const selectSlashCommand = useCallback((command: SlashCommand) => {
    setMessage(`/${command.name} `);
    setSlashDismissed(true);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);
  const slashPalette = useSlashCommandPalette({
    commands: slashCommands,
    query: slashDismissed ? "" : message,
    onSelect: selectSlashCommand,
    onDismiss: () => setSlashDismissed(true),
  });

  useEffect(() => {
    if (!isLoading && pendingRef.current) {
      setMessage("");
      setImages([]);
      setImageError(null);
      pendingRef.current = false;
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    }
  }, [isLoading]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = message.trim();
    const hasImages = images.length > 0;
    if ((trimmed.length > 0 || hasImages) && !isLoading && !disabled) {
      const slashMatch = !hasImages ? trimmed.match(/^\/([a-z][a-z0-9_-]*)(?:\s+(.*))?$/i) : null;
      if (slashMatch && onSlashCommand?.(slashMatch[1].toLowerCase(), (slashMatch[2] ?? "").trim())) {
        setMessage("");
        setSlashDismissed(false);
        if (textareaRef.current) textareaRef.current.style.height = "auto";
        return;
      }
      onSend(
        trimmed.length > 0 ? trimmed : IMAGE_ONLY_PROMPT,
        hasImages ? images : undefined,
      );
      pendingRef.current = true;
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    slashPalette.onKeyDown(e);
    if (e.defaultPrevented) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as FormEvent);
    }
  }

  function handleFileSelect(e: ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files) return;

    setImageError(null);
    const remaining = MAX_IMAGES - images.length;
    if (remaining <= 0) {
      setImageError(`Maximum ${MAX_IMAGES} images allowed`);
      return;
    }

    const filesToProcess = Array.from(files).slice(0, remaining);

    for (const file of filesToProcess) {
      if (!ACCEPTED_TYPES.includes(file.type)) {
        setImageError("Only JPEG, PNG, GIF, and WebP images are accepted");
        continue;
      }
      if (file.size > MAX_FILE_SIZE) {
        setImageError("Images must be under 5MB");
        continue;
      }

      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        // Strip the data URL prefix to get pure base64
        const base64 = result.split(",")[1];
        if (base64) {
          setImages((prev) => {
            if (prev.length >= MAX_IMAGES) return prev;
            return [...prev, base64];
          });
        }
      };
      reader.readAsDataURL(file);
    }

    // Reset file input so the same file can be selected again
    e.target.value = "";
  }

  function removeImage(index: number) {
    setImages((prev) => prev.filter((_, i) => i !== index));
    setImageError(null);
  }

  function handleZipSelect(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip") || !["application/zip", "application/x-zip-compressed", ""].includes(file.type)) {
      setZipError("Choose a ZIP archive");
      return;
    }
    if (file.size <= 0 || file.size > MAX_ZIP_SIZE) {
      setZipError("ZIP files must be between 1 byte and 500MB");
      return;
    }
    setZipFile(file);
    setZipProgress(null);
    setZipError(null);
  }

  async function startZipUpload() {
    if (!zipFile || !zipUploadTarget || zipAbortRef.current) return;
    const controller = new AbortController();
    zipAbortRef.current = controller;
    setZipProgress(0);
    setZipError(null);
    try {
      const result = await uploadZip({
        file: zipFile,
        projectId: zipUploadTarget.projectId,
        platform: zipUploadTarget.platform,
        signal: controller.signal,
        onProgress: setZipProgress,
      });
      await onZipUploaded?.(result.upload_receipt);
      setZipFile(null);
      setZipProgress(null);
    } catch (error) {
      setZipProgress(null);
      setZipError(error instanceof DOMException && error.name === "AbortError" ? "Upload cancelled" : "ZIP upload failed. Retry when ready.");
    } finally {
      zipAbortRef.current = null;
    }
  }

  function cancelZipUpload() {
    zipAbortRef.current?.abort();
  }

  const canSend =
    (message.trim().length > 0 || images.length > 0) && !isLoading && !disabled;

  return (
    <div
      className="px-3 sm:px-6 pb-3 sm:pb-5 pt-2 w-full bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 border-t border-border/60"
      data-tour="chat-input"
    >
      <form onSubmit={handleSubmit}>
        <div
          className={cn(
            "relative rounded-2xl border bg-background shadow-sm transition-shadow",
            "focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-1",
          )}
        >
          <SlashCommandPalette
            commands={slashCommands}
            query={slashDismissed ? "" : message}
            onSelect={selectSlashCommand}
            onDismiss={() => setSlashDismissed(true)}
            state={slashPalette}
            className="absolute inset-x-0 bottom-full z-30 mb-2 max-h-80 overflow-y-auto"
          />
          {/* Image preview strip */}
          {images.length > 0 && (
            <div className="flex items-center gap-2 px-3 pt-2">
              {images.map((img, i) => (
                <div key={i} className="relative group/img">
                  <img
                    src={`data:image/jpeg;base64,${img}`}
                    alt={`Attachment ${i + 1}`}
                    className="h-16 w-16 rounded-lg object-cover border"
                  />
                  <button
                    type="button"
                    onClick={() => removeImage(i)}
                    aria-label={`Remove attachment ${i + 1}`}
                    className="absolute -top-1.5 -right-1.5 h-5 w-5 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center opacity-0 group-hover/img:opacity-100 transition-opacity"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Image error */}
          {imageError && (
            <div className="px-4 pt-2 text-xs text-destructive">
              {imageError}
            </div>
          )}
          {zipFile && (
            <div className="mx-3 mt-2 flex items-center gap-2 border border-border px-2 py-1.5 text-xs">
              <FileArchive className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{zipFile.name}</span>
              {zipProgress !== null && <span>{zipProgress}%</span>}
              {zipProgress !== null ? (
                <button type="button" onClick={cancelZipUpload} aria-label="Cancel ZIP upload" title="Cancel upload" className="p-1 text-muted-foreground hover:text-foreground">
                  <Square className="h-3.5 w-3.5" />
                </button>
              ) : (
                <>
                  <button type="button" onClick={startZipUpload} disabled={!zipUploadTarget} className="px-1.5 py-1 text-primary hover:bg-muted disabled:text-muted-foreground">
                    Upload
                  </button>
                  <button type="button" onClick={() => { setZipFile(null); setZipError(null); }} aria-label="Remove ZIP" title="Remove ZIP" className="p-1 text-muted-foreground hover:text-foreground">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </>
              )}
            </div>
          )}
          {zipError && <div className="px-4 pt-2 text-xs text-destructive">{zipError}</div>}

          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => {
              setMessage(e.target.value);
              setSlashDismissed(false);
            }}
            onKeyDown={handleKeyDown}
            placeholder={placeholder ?? "Ask about your documents..."}
            rows={1}
            className={cn(
              "w-full resize-none bg-transparent px-4 pt-3 pb-2",
              "text-base sm:text-sm placeholder:text-muted-foreground",
              "focus:outline-none",
              "disabled:cursor-not-allowed disabled:opacity-50",
              "min-h-[44px] max-h-[120px] sm:max-h-[200px]",
            )}
            disabled={isLoading || disabled}
            style={{ height: "auto", overflow: "hidden" }}
            onInput={(e) => {
              const target = e.target as HTMLTextAreaElement;
              const max = window.matchMedia("(min-width: 640px)").matches ? 200 : 120;
              target.style.height = "auto";
              target.style.height =
                Math.min(target.scrollHeight, max) + "px";
            }}
          />

          {/* Hidden file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/gif,image/webp"
            multiple
            className="hidden"
            onChange={handleFileSelect}
          />
          <input
            ref={zipInputRef}
            type="file"
            accept=".zip,application/zip,application/x-zip-compressed"
            className="hidden"
            onChange={handleZipSelect}
          />

          {/* Bottom row: toolbar + image button + send button.
              min-w-0 + wrapping keeps wide toolbar content (mode toggle, project
              picker, runtime status) inside the composer instead of pushing the
              send button past its right edge. */}
          <div className="flex items-end justify-between gap-2 px-3 pb-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
              {toolbarContent}
              {!hideImagePicker && (
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isLoading || disabled || images.length >= MAX_IMAGES}
                  aria-label="Attach image"
                  className={cn(
                    "flex shrink-0 items-center justify-center w-7 h-7 rounded-md transition-colors",
                    images.length >= MAX_IMAGES
                      ? "text-muted-foreground/40 cursor-not-allowed"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted",
                  )}
                  title={images.length >= MAX_IMAGES ? `Max ${MAX_IMAGES} images` : "Attach image"}
                >
                  <ImagePlus className="h-4 w-4" />
                </button>
              )}
              {onZipUploaded && (
                <button
                  type="button"
                  onClick={() => zipInputRef.current?.click()}
                  disabled={isLoading || disabled || zipProgress !== null}
                  aria-label="Attach ZIP source"
                  title="Attach ZIP source"
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <FileArchive className="h-4 w-4" />
                </button>
              )}
            </div>
            <button
              type="submit"
              disabled={!canSend}
              aria-label="Send message"
              className={cn(
                "flex shrink-0 items-center justify-center w-10 h-10 sm:w-8 sm:h-8 rounded-full transition-colors",
                canSend
                  ? "bg-primary text-primary-foreground hover:bg-primary/90"
                  : "bg-muted text-muted-foreground cursor-not-allowed",
              )}
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ArrowUp className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
