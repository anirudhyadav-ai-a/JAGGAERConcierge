import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Claude writes answers in Markdown (bold, lists, tables). react-markdown builds React elements from it; without
 * the rehype-raw plugin, any raw HTML in the text is shown as plain text, never turned into elements.
 */
export function Answer({ text }: { text: string }) {
  return (
    <div className="a md">
      <Markdown remarkPlugins={[remarkGfm]}>
        {text}
      </Markdown>
    </div>
  );
}
