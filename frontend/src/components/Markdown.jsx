/**
 * Affichage des réponses du copilote, rédigées en Markdown par le LLM
 * (tableaux, gras, listes). Avant, le texte brut était affiché tel quel :
 * astérisques partout et tableaux illisibles (« | --- | »).
 *
 * Volontairement minimal et SANS dépendance (pas de react-markdown) : aucun
 * paquet supplémentaire à télécharger au build, et aucun HTML injecté — tout
 * est rendu en éléments React, donc pas de risque XSS si le LLM renvoie du HTML.
 * Couvre ce que le copilote produit : titres, paragraphes, listes à puces ou
 * numérotées, tableaux, **gras**, *italique*, `code`.
 */

const LIGNE_TABLEAU = /^\s*\|.*\|\s*$/;
const SEPARATEUR_TABLEAU = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const PUCE = /^\s*[-*•]\s+(.*)$/;
const NUMERO = /^\s*\d+[.)]\s+(.*)$/;
const TITRE = /^\s*(#{1,4})\s+(.*)$/;
const EN_LIGNE = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|`[^`]+`)/g;

/** Gras, italique et code à l'intérieur d'une ligne. */
export function enLigne(texte) {
  return String(texte)
    .split(EN_LIGNE)
    .filter((morceau) => morceau !== "")
    .map((morceau, i) => {
      if (/^(\*\*|__).+(\*\*|__)$/.test(morceau)) return <strong key={i}>{morceau.slice(2, -2)}</strong>;
      if (/^`.+`$/.test(morceau))
        return (
          <code key={i} className="rounded bg-surface px-1 font-mono text-[0.9em]">
            {morceau.slice(1, -1)}
          </code>
        );
      if (/^\*.+\*$/.test(morceau)) return <em key={i}>{morceau.slice(1, -1)}</em>;
      return morceau;
    });
}

const cellules = (ligne) =>
  ligne
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

/** Découpe le texte en blocs : titre, paragraphe, liste, tableau. */
export function analyser(texte) {
  const lignes = String(texte ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocs = [];
  let i = 0;
  while (i < lignes.length) {
    const ligne = lignes[i];
    if (!ligne.trim()) {
      i += 1;
      continue;
    }
    if (LIGNE_TABLEAU.test(ligne) && i + 1 < lignes.length && SEPARATEUR_TABLEAU.test(lignes[i + 1])) {
      const entetes = cellules(ligne);
      const rangees = [];
      i += 2;
      while (i < lignes.length && LIGNE_TABLEAU.test(lignes[i])) rangees.push(cellules(lignes[i++]));
      blocs.push({ type: "tableau", entetes, rangees });
      continue;
    }
    const titre = ligne.match(TITRE);
    if (titre) {
      blocs.push({ type: "titre", texte: titre[2] });
      i += 1;
      continue;
    }
    if (PUCE.test(ligne) || NUMERO.test(ligne)) {
      const ordonnee = NUMERO.test(ligne) && !PUCE.test(ligne);
      const motif = ordonnee ? NUMERO : PUCE;
      const elements = [];
      while (i < lignes.length && motif.test(lignes[i])) {
        let element = lignes[i].match(motif)[1];
        i += 1;
        // ligne de continuation indentée (sous-texte d'un élément)
        while (i < lignes.length && /^\s{2,}\S/.test(lignes[i]) && !PUCE.test(lignes[i]) && !NUMERO.test(lignes[i])) {
          element += ` ${lignes[i].trim()}`;
          i += 1;
        }
        elements.push(element);
      }
      blocs.push({ type: ordonnee ? "liste-numerotee" : "liste", elements });
      continue;
    }
    const paragraphe = [ligne.trim()];
    i += 1;
    while (
      i < lignes.length &&
      lignes[i].trim() &&
      !LIGNE_TABLEAU.test(lignes[i]) &&
      !TITRE.test(lignes[i]) &&
      !PUCE.test(lignes[i]) &&
      !NUMERO.test(lignes[i])
    ) {
      paragraphe.push(lignes[i].trim());
      i += 1;
    }
    blocs.push({ type: "paragraphe", texte: paragraphe.join(" ") });
  }
  return blocs;
}

export function Markdown({ texte }) {
  return (
    <div className="space-y-2.5">
      {analyser(texte).map((bloc, i) => {
        if (bloc.type === "titre")
          return (
            <p key={i} className="font-display text-[15px] font-bold text-ink">
              {enLigne(bloc.texte)}
            </p>
          );
        if (bloc.type === "liste")
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {bloc.elements.map((e, j) => (
                <li key={j}>{enLigne(e)}</li>
              ))}
            </ul>
          );
        if (bloc.type === "liste-numerotee")
          return (
            <ol key={i} className="list-decimal space-y-1 pl-5">
              {bloc.elements.map((e, j) => (
                <li key={j}>{enLigne(e)}</li>
              ))}
            </ol>
          );
        if (bloc.type === "tableau")
          return (
            <div key={i} className="overflow-x-auto rounded-xl border border-line bg-surface">
              <table className="w-full border-collapse text-[13px]">
                <thead className="bg-brand-light text-left text-ink">
                  <tr>
                    {bloc.entetes.map((h, j) => (
                      <th key={j} className="px-3 py-2 font-semibold">
                        {enLigne(h)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {bloc.rangees.map((rangee, j) => (
                    <tr key={j} className="border-t border-line">
                      {bloc.entetes.map((_, k) => (
                        <td key={k} className={`px-3 py-2 align-top ${k > 0 ? "font-tabular" : ""}`}>
                          {enLigne(rangee[k] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        return <p key={i}>{enLigne(bloc.texte)}</p>;
      })}
    </div>
  );
}
