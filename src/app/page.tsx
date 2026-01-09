import Link from "next/link";
import { getAllPostsMeta } from "@/lib/mdx";

export default function Home() {
  const posts = getAllPostsMeta();
  const latestPost = posts[0];

  return (
    <main className="min-h-screen bg-background">
      <div className="max-w-3xl mx-auto px-6 py-16">
        {/* Cards */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {/* Blog Card */}
          <Link
            href="/blog"
            className="group block p-5 aspect-square bg-gray-50 dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl transition-all hover:border-gray-300 dark:hover:border-gray-600 hover:-translate-y-0.5 flex flex-col"
          >
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-auto">Blog</span>
            {latestPost ? (
              <p className="font-medium line-clamp-3">{latestPost.title}</p>
            ) : (
              <p className="text-gray-500">Coming soon</p>
            )}
          </Link>

          {/* Projects Card */}
          <Link
            href="/projects"
            className="group block p-5 aspect-square bg-gray-50 dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl transition-all hover:border-gray-300 dark:hover:border-gray-600 hover:-translate-y-0.5 flex flex-col"
          >
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-auto">Projects</span>
            <p className="font-medium line-clamp-3">Side projects and open source</p>
          </Link>

          {/* About Card */}
          <Link
            href="/about"
            className="group block p-5 aspect-square bg-gray-50 dark:bg-gray-900 border border-gray-100 dark:border-gray-800 rounded-xl transition-all hover:border-gray-300 dark:hover:border-gray-600 hover:-translate-y-0.5 flex flex-col"
          >
            <span className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-auto">About</span>
            <p className="font-medium line-clamp-3">Background and experience</p>
          </Link>
        </div>

        {/* Footer */}
        <footer className="mt-16 pt-8 border-t border-gray-100 dark:border-gray-800">
          <div className="flex gap-6 text-sm text-gray-500 dark:text-gray-400">
            <a
              href="https://linkedin.com/in/jiankuang"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
            >
              LinkedIn
            </a>
            <a
              href="https://github.com/jkuang7"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
            >
              GitHub
            </a>
          </div>
        </footer>
      </div>
    </main>
  );
}
