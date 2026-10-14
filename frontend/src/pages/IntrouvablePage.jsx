import { Link } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { IconTarget } from "../components/icons";

export function IntrouvablePage() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
      <span className="focus-ring flex h-14 w-14 items-center justify-center rounded-full bg-brand-light text-brand">
        <IconTarget width={26} height={26} />
      </span>
      <h1 className="font-display text-2xl font-extrabold text-ink">Page introuvable</h1>
      <p className="max-w-sm text-sm text-muted">
        Cette page n'existe pas ou a été déplacée. Retournez à l'accueil pour continuer.
      </p>
      <Link to="/">
        <Button>Retour à l'accueil</Button>
      </Link>
    </div>
  );
}
