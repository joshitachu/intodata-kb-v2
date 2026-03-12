"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
      <h1 className="text-6xl font-bold text-red-200">Error</h1>
      <h2 className="text-xl font-semibold text-gray-700">Something went wrong</h2>
      <p className="text-gray-500 text-sm max-w-md text-center">
        {error.message || "An unexpected error occurred. Please try again."}
      </p>
      <button
        onClick={reset}
        className="mt-4 px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700"
      >
        Try Again
      </button>
    </div>
  );
}
