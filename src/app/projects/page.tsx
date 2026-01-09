import { ExternalLink, Github } from "lucide-react";

export const metadata = {
  title: "Projects | Jian Kuang",
  description: "Side projects and open source work",
};

/* PLACEHOLDER - Replace with real projects */
const projects = [
  {
    name: "Project Alpha",
    description: "A tool that does X. Built to solve Y problem.",
    tech: ["React", "TypeScript", "Node"],
    github: "#",
    demo: "#",
  },
  {
    name: "Project Beta",
    description: "CLI utility for Z. Makes developers more productive.",
    tech: ["Go", "SQLite"],
    github: "#",
    demo: null,
  },
  {
    name: "Project Gamma",
    description: "Open source contribution to popular library.",
    tech: ["Python", "FastAPI"],
    github: "#",
    demo: null,
  },
];

export default function ProjectsPage() {
  return (
    <main className="max-w-3xl mx-auto px-4 py-12">
      <header className="mb-12">
        <h1 className="text-4xl font-bold mb-4">Projects</h1>
        <p className="text-gray-600 dark:text-gray-400 text-lg">
          Side projects and open source work.
        </p>
      </header>

      <div className="grid gap-6 sm:grid-cols-2">
        {projects.map((project) => (
          <article
            key={project.name}
            className="group border border-gray-200 dark:border-gray-800 rounded-lg p-6 hover:border-gray-300 dark:hover:border-gray-700 hover:shadow-sm transition-all"
          >
            <h2 className="text-xl font-semibold mb-2 group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">
              {project.name}
            </h2>
            <p className="text-gray-600 dark:text-gray-400 text-sm mb-4 leading-relaxed">
              {project.description}
            </p>

            <div className="flex flex-wrap gap-2 mb-4">
              {project.tech.map((tech) => (
                <span
                  key={tech}
                  className="px-2 py-0.5 bg-gray-100 dark:bg-gray-800 rounded text-xs font-medium text-gray-600 dark:text-gray-400"
                >
                  {tech}
                </span>
              ))}
            </div>

            <div className="flex gap-4">
              <a
                href={project.github}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
              >
                <Github className="w-4 h-4" />
                <span>GitHub</span>
              </a>
              {project.demo && (
                <a
                  href={project.demo}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
                >
                  <ExternalLink className="w-4 h-4" />
                  <span>Demo</span>
                </a>
              )}
            </div>
          </article>
        ))}
      </div>
    </main>
  );
}
