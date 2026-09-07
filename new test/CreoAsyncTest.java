import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcSolid.*;
import com.ptc.pfc.pfcAssembly.*;
import com.ptc.pfc.pfcFeature.*;
import com.ptc.pfc.pfcComponentFeat.*;
import java.io.PrintStream;
import java.io.FileWriter;

public class CreoAsyncTest {

    static {
        System.loadLibrary("pfcasyncmt");
    }

    public static void main(String[] args) {
        AsyncConnection connection = null;
        Session session = null;

        try {
            System.setOut(new PrintStream(System.out, true, "UTF-8"));
            System.setErr(new PrintStream(System.err, true, "UTF-8"));

            System.out.println("Tentative de connexion a la session Creo active...");
            connection = pfcAsyncConnection.AsyncConnection_Connect(null, null, null, 10);
            
            session = connection.GetSession();
            System.out.println("=========================================");
            System.out.println("SUCCESS: Connecte a la session Creo existante !");
            System.out.println("=========================================");

            Model currentModel = session.GetActiveModel();
            
            if (currentModel == null) {
                System.out.println("Aucun modele visible. Ouvrez un assemblage dans Creo.");
            } else if (currentModel.GetType().getValue() == ModelType._MDL_ASSEMBLY) {
                Assembly assembly = (Assembly) currentModel;
                System.out.println("Extraction de l'assemblage : " + assembly.GetFileName());
                
                // 1. Initialiser la construction du JSON
                StringBuilder jsonBuilder = new StringBuilder();
                
                // 2. Lancer la racine de l'arbre
                buildJsonTree(session, assembly, jsonBuilder, 0);
                
                String jsonOutput = jsonBuilder.toString();
                
                // 3. Afficher le JSON dans la console
                System.out.println("\n--- Fichier JSON Genere ---");
                System.out.println(jsonOutput);
                System.out.println("---------------------------\n");
                
                // 4. Sauvegarder dans un fichier physique
                try (FileWriter file = new FileWriter("assembly_structure.json")) {
                    file.write(jsonOutput);
                    System.out.println("SUCCESS: Fichier 'assembly_structure.json' cree avec succes.");
                }
                
            } else {
                System.out.println("Le modele n'est pas un assemblage.");
            }

            System.out.println("Deconnexion du script...");
            connection.Disconnect(10); 
            System.out.println("Deconnecte proprement.");

        } catch (Exception e) {
            System.err.println("Erreur : " + e.getMessage());
            e.printStackTrace();
        }
    }

    private static void buildJsonTree(Session session, Assembly assy, StringBuilder sb, int depth) throws Exception {
        String indent = getIndent(depth);
        
        sb.append(indent).append("{\n");
        sb.append(indent).append("  \"name\": \"").append(assy.GetFileName()).append("\",\n");
        sb.append(indent).append("  \"type\": \"ASM\",\n");
        sb.append(indent).append("  \"children\": [\n");

        Features features = assy.ListFeaturesByType(null, null);
        boolean firstChild = true;

        if (features != null) {
            for (int i = 0; i < features.getarraysize(); i++) {
                Feature feat = features.get(i);

                if (feat instanceof ComponentFeat) {
                    ComponentFeat compFeat = (ComponentFeat) feat;
                    ModelDescriptor descr = compFeat.GetModelDescr();
                    
                    if (descr != null) {
                        if (!firstChild) {
                            sb.append(",\n");
                        }
                        firstChild = false;

                        String compName = descr.GetInstanceName();
                        
                        if (descr.GetType().getValue() == ModelType._MDL_ASSEMBLY) {
                            // Si c'est un sous-assemblage, on descend récursivement
                            try {
                                Model subModel = session.GetModelFromDescr(descr);
                                if (subModel instanceof Assembly) {
                                    buildJsonTree(session, (Assembly) subModel, sb, depth + 2);
                                } else {
                                    appendLeafNode(sb, compName, "ASM", depth + 2);
                                }
                            } catch (Exception e) {
                                appendLeafNode(sb, compName, "ASM_UNLOADED", depth + 2);
                            }
                        } else {
                            // Si c'est une pièce (Part), c'est une feuille de l'arbre
                            appendLeafNode(sb, compName, "PRT", depth + 2);
                        }
                    }
                }
            }
        }

        sb.append("\n").append(indent).append("  ]\n");
        sb.append(indent).append("}");
    }

    private static void appendLeafNode(StringBuilder sb, String name, String type, int depth) {
        String indent = getIndent(depth);
        sb.append(indent).append("{\n");
        sb.append(indent).append("  \"name\": \"").append(name).append("\",\n");
        sb.append(indent).append("  \"type\": \"").append(type).append("\",\n");
        sb.append(indent).append("  \"children\": []\n");
        sb.append(indent).append("}");
    }

    private static String getIndent(int depth) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < depth; i++) {
            sb.append("  ");
        }
        return sb.toString();
    }
}
