import com.ptc.pfc.pfcGlobal.pfcGlobal;
import com.ptc.pfc.pfcSession.*;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcCommand.*;

public class CreoRibbonButton {

    // Global variable to keep track of the command action
    private static UICommand cmd;

    // Called automatically by Creo during boot sequence
    public static void start() {
        try {
            Session session = pfcGlobal.GetProESession();
            
            // Register the command ID. Creo maps this exact string to your creo_ribbon.ui
            cmd = session.UICreateCommand("GetActivePartCmd", new GetPartNameListener());
            cmd.Designate(
                "creoribbonbutton.txt",
                "GetActivePartCmd",
                "GetActivePartCmdHelp",
                "GetActivePartCmd"  // description key; can reuse label for now
            );
            
        } catch (Exception e) {
            System.out.println("J-Link Start Error: " + e.getMessage());
            e.printStackTrace();
        }
    }

    // Called automatically when Creo closes
    public static void stop() {
        // Safe cleanup
    }

    // Listener executed when the user clicks the button
    private static class GetPartNameListener extends DefaultUICommandActionListener {
        public void OnCommand() {
            try {
                Session session = pfcGlobal.GetProESession();
                Model activeModel = session.GetActiveModel();

                String message;
                if (activeModel == null) {
                    message = "No active model open in this session.";
                } else {
                    message = "Active Model Name: " + activeModel.GetFileName() +
                              "\nType: " + activeModel.GetType().toString();
                }

                // Show a native Creo graphic dialog alert box
                session.UIShowMessageDialog(message, null);

            } catch (Exception e) {
                System.out.println("Button Click Execution Error: " + e.getMessage());
                e.printStackTrace();
            }
        }
    }
}
