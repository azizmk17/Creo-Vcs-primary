import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcModel.DefaultModelActionListener;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModelItem.Parameter;
import com.ptc.pfc.pfcModelItem.ParamValue;

/** Guards model parameters and refreshes protection as session models change. */
public class NexusModelMutationGuard extends DefaultModelActionListener {
    public void OnBeforeParameterCreate(Model owner, String name, ParamValue value)
        throws jxthrowable {
        NexusJLink.requireModelEdit(owner, "parameter creation");
    }

    public void OnBeforeParameterModify(Parameter parameter, ParamValue value)
        throws jxthrowable {
        NexusJLink.requireModelEdit(
            NexusJLink.modelForChild(parameter), "parameter modification"
        );
    }

    public void OnBeforeParameterDelete(Parameter parameter) throws jxthrowable {
        NexusJLink.requireModelEdit(
            NexusJLink.modelForChild(parameter), "parameter deletion"
        );
    }

    public void OnAfterModelRetrieve(Model model) throws jxthrowable {
        NexusJLink.refreshCommandProtection();
    }

    public void OnAfterModelCreate(Model model) throws jxthrowable {
        NexusJLink.refreshCommandProtection();
    }

    public void OnBeforeModelDisplay(Model model) throws jxthrowable {
        NexusJLink.refreshCommandProtection();
    }
}
