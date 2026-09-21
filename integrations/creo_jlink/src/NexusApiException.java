public class NexusApiException extends Exception {
    private static final long serialVersionUID = 1L;

    private final int status;
    private final String code;

    public NexusApiException(int status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code == null ? "" : code;
    }

    public int getStatus() {
        return status;
    }

    public String getCode() {
        return code;
    }
}
