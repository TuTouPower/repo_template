import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// ---------------------------------------------------------------------------
// Markdown 渲染(spec.md / task.md 等任务文档,浅色/深色自适应)
// ---------------------------------------------------------------------------

export function MarkdownView({ markdown }: { markdown: string }) {
  return (
    <div className="text-[13px] leading-6 text-stone-700 dark:text-stone-300">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="mb-3 border-b border-stone-200 pb-2 text-base font-semibold text-stone-900 dark:border-stone-700 dark:text-stone-100">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-5 text-sm font-semibold text-stone-800 dark:text-stone-200">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1.5 mt-4 text-[13px] font-semibold text-stone-800 dark:text-stone-200">
              {children}
            </h3>
          ),
          p: ({ children }) => <p className="mb-2.5">{children}</p>,
          ul: ({ children }) => (
            <ul className="mb-3 list-disc space-y-1 pl-5 marker:text-stone-400">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-3 list-decimal space-y-1 pl-5 marker:text-stone-400">{children}</ol>
          ),
          blockquote: ({ children }) => (
            <blockquote className="mb-3 border-l-2 border-stone-300 pl-3 text-stone-500 dark:border-stone-600 dark:text-stone-400">
              {children}
            </blockquote>
          ),
          code: ({ className, children }) => {
            const isBlock = className?.startsWith('language-')
            if (isBlock) {
              return (
                <code className="block overflow-x-auto rounded-lg bg-stone-900 p-3 font-mono text-[11.5px] leading-5 text-stone-100 dark:bg-stone-950">
                  {children}
                </code>
              )
            }
            return (
              <code className="rounded bg-stone-100 px-1 py-0.5 font-mono text-[11.5px] text-stone-800 dark:bg-stone-800 dark:text-stone-200">
                {children}
              </code>
            )
          },
          pre: ({ children }) => <pre className="mb-3">{children}</pre>,
          table: ({ children }) => (
            <div className="mb-3 overflow-x-auto rounded-lg border border-stone-200 dark:border-stone-700">
              <table className="w-full border-collapse text-[12px]">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-stone-50 dark:bg-stone-800">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border-b border-stone-200 px-3 py-1.5 text-left font-semibold text-stone-700 dark:border-stone-700 dark:text-stone-300">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-stone-100 px-3 py-1.5 text-stone-600 dark:border-stone-800 dark:text-stone-400">
              {children}
            </td>
          ),
          hr: () => <hr className="my-4 border-stone-200 dark:border-stone-700" />,
          a: ({ children, href }) => (
            <a href={href} className="text-sky-700 underline underline-offset-2 dark:text-sky-400">
              {children}
            </a>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-stone-900 dark:text-stone-100">{children}</strong>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
