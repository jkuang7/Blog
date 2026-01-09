import Link from "next/link";

export function Header() {
  return (
    <header className="border-b border-gray-200 dark:border-gray-800">
      <nav className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
        <Link href="/" className="text-xl font-bold hover:opacity-80">
          Jian Kuang
        </Link>
        <div className="flex gap-6">
          <Link
            href="/blog"
            className="text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100"
          >
            Blog
          </Link>
          <Link
            href="/projects"
            className="text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100"
          >
            Projects
          </Link>
        </div>
      </nav>
    </header>
  );
}
