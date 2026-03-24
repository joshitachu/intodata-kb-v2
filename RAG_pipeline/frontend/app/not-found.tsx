import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
      <h1 className="text-6xl font-bold text-gray-300">404</h1>
      <h2 className="text-xl font-semibold text-gray-700">Page Not Found</h2>
      <p className="text-gray-500 text-sm">
        The page you are looking for does not exist or has been moved.
      </p>
      <Link
        href="/"
        className="mt-4 px-4 py-2 bg-[#001e4d] text-white text-sm rounded-md hover:bg-[#001e4d]/90"
      >
        Go Home
      </Link>
    </div>
  );
}
