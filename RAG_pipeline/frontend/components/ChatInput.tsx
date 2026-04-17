"use client";

import {
  useState,
  useRef,
  useEffect,
  type FormEvent,
  type KeyboardEvent,
  type ChangeEvent,
  type ReactNode,
} from "react";
import { ArrowUp, Loader2, ImagePlus, X } from "lucide-react";
import { cn } from "@/lib/utils";

const MAX_IMAGES = 4;
const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];

interface ChatInputProps {
  onSend: (message: string, images?: string[]) => void;
  isLoading: boolean;
  disabled?: boolean;
  placeholder?: string;
  toolbarContent?: ReactNode;
  hideImagePicker?: boolean;
}

export function ChatInput({
  onSend,
  isLoading,
  disabled,
  placeholder,
  toolbarContent,
  hideImagePicker,
}: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const pendingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
    if (trimmed.length > 0 && !isLoading && !disabled) {
      onSend(trimmed, images.length > 0 ? images : undefined);
      pendingRef.current = true;
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
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

  const canSend = message.trim().length > 0 && !isLoading && !disabled;

  return (
    <div className="px-6 pb-5 pt-2 w-full">
      <form onSubmit={handleSubmit}>
        <div
          className={cn(
            "relative rounded-2xl border bg-background shadow-sm transition-shadow",
            "focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-1",
          )}
        >
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

          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder ?? "Ask about your documents..."}
            rows={1}
            className={cn(
              "w-full resize-none bg-transparent px-4 pt-3 pb-2",
              "text-sm placeholder:text-muted-foreground",
              "focus:outline-none",
              "disabled:cursor-not-allowed disabled:opacity-50",
              "min-h-[44px] max-h-[200px]",
            )}
            disabled={isLoading || disabled}
            style={{ height: "auto", overflow: "hidden" }}
            onInput={(e) => {
              const target = e.target as HTMLTextAreaElement;
              target.style.height = "auto";
              target.style.height =
                Math.min(target.scrollHeight, 200) + "px";
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

          {/* Bottom row: toolbar + image button + send button */}
          <div className="flex items-center justify-between px-3 pb-2">
            <div className="flex items-center gap-2">
              {toolbarContent}
              {!hideImagePicker && (
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isLoading || disabled || images.length >= MAX_IMAGES}
                  className={cn(
                    "flex items-center justify-center w-7 h-7 rounded-md transition-colors",
                    images.length >= MAX_IMAGES
                      ? "text-muted-foreground/40 cursor-not-allowed"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted",
                  )}
                  title={images.length >= MAX_IMAGES ? `Max ${MAX_IMAGES} images` : "Attach image"}
                >
                  <ImagePlus className="h-4 w-4" />
                </button>
              )}
            </div>
            <button
              type="submit"
              disabled={!canSend}
              className={cn(
                "flex items-center justify-center w-8 h-8 rounded-full transition-colors",
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
