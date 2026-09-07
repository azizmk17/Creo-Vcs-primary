import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSolid.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcComponentFeat.*;

public class CreoAsyncTest {

    static {
        System.loadLibrary("pfcasyncmt");
    }

    public static void main(String[] args) {
        AsyncConnection connection = null;
        Session session = null;

        try {
            System.out.println("Tentative de connexion à la session Creo active...");
            connection = pfcAsyncConnection.AsyncConnection_Connect(null, null, null, 10);
            
            session = connection.GetSession();
            System.out.println("=========================================");
            System.out.println("SUCCESS: Connecté à la session Creo existante !");
            System.out.println("=========================================");

            // Utilisation de GetActiveModel() qui est la méthode standard vérifiée
            Model currentModel = session.GetActiveModel();
            
            if (currentModel == null) {
                System.out.println("Aucun modèle visible. Ouvrez un assemblage dans Creo.");
            } else if (currentModel.GetType() == ModelType.MDL_ASSEMBLY) {
                Assembly assembly = (Assembly) currentModel;
                System.out.println("Structure de l'assemblage actif : " + assembly.GetFileName());
                System.out.println("-----------------------------------------");
                
                // Lancement de l'extraction
                extractAssemblyTree(session, assembly, 0);
                
                System.out.println("-----------------------------------------");
                System.out.println("Fin de l'extraction de l'arbre.");
            } else {
                System.out.println("Le modèle n'est pas un assemblage (Type : " + currentModel.GetType() + ").");
            }

            System.out.println("Déconnexion du script...");
            connection.Disconnect(10); 
            System.out.println("Déconnecté proprement.");

        } catch (Exception e) {
            System.err.println("Erreur : " + e.getMessage());
            e.printStackTrace();
        }
    }

    private static void extractAssemblyTree(Session session, Assembly assy, int depth) throws Exception {
        // Remplacement par ListFeaturesByType(null, null) pour obtenir toutes les features
        Features features = assy.ListFeaturesByType(null, null);
        if (features == null) return;

        String indent = "";
        for (int i = 0; i < depth; i++) {
            indent += "  |-- ";
        }

        for (int i = 0; i < features.getarraysize(); i++) {
            Feature feat = features.get(i);

            // Vérification si la fonctionnalité est un composant de l'assemblage
            if (feat instanceof ComponentFeat) {
                ComponentFeat compFeat = (ComponentFeat) feat;
                ModelDescriptor descr = compFeat.GetModelDescr();
                
                if (descr != null) {
                    System.out.println(indent + descr.GetInstanceName() + " (" + descr.GetType() + ")");
                    
                    // Si c'est un sous-assemblage, on parcourt son sous-arbre
                    if (descr.GetType() == ModelType.MDL_ASSEMBLY) {
                        try {
                            Model subModel = session.GetModelFromDescr(descr);
                            if (subModel instanceof Assembly) {
                                extractAssemblyTree(session, (Assembly) subModel, depth + 1);
                            }
                        } catch (Exception e) {
                            System.out.println(indent + "  [Sous-assemblage non chargé en mémoire]");
                        }
                    }
                }
            }
        }
    }
}
