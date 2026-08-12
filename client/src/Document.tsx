import Editor from "./Editor";

export interface DocumentProps {
  onContentChange: (content: string) => void;
  content: string;
}

export default function Document({ onContentChange, content }: DocumentProps) {
  return (
    <div className="w-full h-full overflow-y-auto">
      <Editor handleEditorChange={onContentChange} content={content} />
    </div>
  );
}
