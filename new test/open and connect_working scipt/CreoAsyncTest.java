import com.ptc.pfc.pfcAsyncConnection.*;
import com.ptc.pfc.pfcSession.*;
import java.io.File;

public class CreoAsyncTest {

    static {
        // Charger la DLL native obligatoire pour le mode asynchrone autonome
        System.loadLibrary("pfcasyncmt");
    }

    public static void main(String[] args) {
        AsyncConnection connection = null;
        Session session = null;

        File batFile = new File("run_creo_jlink.bat");
        String batAbsolutePath = batFile.getAbsolutePath();

        // On combine la commande d'appel Windows et le script en une seule chaîne
        String fullCmdLine = "C:\\Windows\\System32\\cmd.exe /c \"" + batAbsolutePath + "\"";
        
        try {
            System.out.println("Checking batch file path: " + batAbsolutePath);
            if (!batFile.exists()) {
                System.err.println("ERROR: run_creo_jlink.bat not found!");
                return;
            }

            System.out.println("Executing command line: " + fullCmdLine);
            System.out.println("Launching Creo Parametric...");

            // Utilisation de la méthode à 2 arguments supportée par votre JAR
            connection = pfcAsyncConnection.AsyncConnection_Start(fullCmdLine, null);
            
            session = connection.GetSession();
            System.out.println("=========================================");
            System.out.println("SUCCESS: Connected to Creo Session via pfcasync!");
            System.out.println("=========================================");

            String currentDir = session.GetCurrentDirectory();
            System.out.println("Current Creo Working Directory: " + currentDir);

            System.out.println("Waiting 10 seconds before disconnecting...");
            Thread.sleep(10000);

            System.out.println("Disconnecting from session...");
            connection.Disconnect(10); 
            System.out.println("Disconnected safely.");

        } catch (Exception e) {
            System.err.println("Exception occurred: " + e.getMessage());
            e.printStackTrace();
        }
    }
}
