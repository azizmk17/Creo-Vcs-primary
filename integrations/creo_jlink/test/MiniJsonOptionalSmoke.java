import java.util.List;
import java.util.Map;

public class MiniJsonOptionalSmoke {
    public static void main(String[] args) {
        Map<String, Object> row = MiniJson.object(
            MiniJson.parse("{\"cad_document_id\":null,\"local_files\":null}")
        );
        if (MiniJson.integer(row, "cad_document_id") != 0) {
            throw new RuntimeException("Expected null integer to read as zero.");
        }
        if (MiniJson.integer(row, "missing") != 0) {
            throw new RuntimeException("Expected missing integer to read as zero.");
        }
        List<Object> values = MiniJson.array(row.get("local_files"));
        if (!values.isEmpty()) {
            throw new RuntimeException("Expected null array to read as empty.");
        }
        System.out.println("MINI_JSON_OPTIONAL_OK");
    }
}
