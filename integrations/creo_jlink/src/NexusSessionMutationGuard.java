import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcSession.DefaultSessionActionListener;
import com.ptc.pfc.pfcWindow.Window;

/** Rebinds mode-specific Creo commands after model or window transitions. */
public class NexusSessionMutationGuard extends DefaultSessionActionListener {
    public void OnAfterModelDisplay() throws jxthrowable {
        NexusJLink.refreshCommandProtection();
    }

    public void OnAfterWindowChange(Window window) throws jxthrowable {
        NexusJLink.refreshCommandProtection();
    }
}
