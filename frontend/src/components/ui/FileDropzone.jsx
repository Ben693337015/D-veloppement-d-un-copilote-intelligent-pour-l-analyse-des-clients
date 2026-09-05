import { useCallback, useRef, useState } from "react";
import { IconUpload } from "../icons";

export function FileDropzone({ onFileSelected, fichierActuel, accept = ".csv,.xlsx,.xls" }) {
  const [survole, setSurvole] = useState(false);
  const inputRef = useRef(null);

  const gererFichiers = useCallback(
    (fichiers) => {
      if (fichiers && fichiers[0]) onFileSelected(fichiers[0]);
    },
    [onFileSelected],
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setSurvole(true);
      }}
      onDragLeave={() => setSurvole(false)}
      onDrop={(e) => {
        e.preventDefault();
        setSurvole(false);
        gererFichiers(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
      className={`flex cursor-pointer flex-col items-center justify-center gap-2.5 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
        survole ? "border-brand bg-brand-light" : "border-line bg-paper/60 hover:border-brand/50"
      }`}
    >
      <span className="focus-ring flex h-11 w-11 items-center justify-center rounded-full bg-brand-light text-brand">
        <IconUpload width={20} height={20} />
      </span>
      {fichierActuel ? (
        <div>
          <p className="text-sm font-semibold text-ink">{fichierActuel.name}</p>
          <p className="text-xs text-muted">{(fichierActuel.size / 1024).toFixed(0)} Ko · cliquez pour remplacer</p>
        </div>
      ) : (
        <div>
          <p className="text-sm font-semibold text-ink">Glissez votre fichier ici, ou cliquez pour parcourir</p>
          <p className="text-xs text-muted">CSV, XLSX ou XLS — un export de caisse, d'ERP ou un tableur</p>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => gererFichiers(e.target.files)}
      />
    </div>
  );
}
