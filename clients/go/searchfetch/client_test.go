package searchfetch

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

func TestClientRetriesServerError(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			_, _ = w.Write([]byte(`{"error":{"message":"temporary","type":"invalid_request_error","param":null,"code":"internal_error"}}`))
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[{"rerank_score":1.5,"title":"Result","url":"https://example.com","snippet":"Snippet","summary":"Summary","content":"","site_name":"example.com","site_icon":null,"date":null,"authority":2}]}`))
	}))
	defer server.Close()

	client := New(server.URL, "key")
	client.MaxRetries = 1
	results, err := client.Search(context.Background(), "query", SearchOptions{})
	if err != nil {
		t.Fatalf("Search returned error: %v", err)
	}
	if calls.Load() != 2 {
		t.Fatalf("expected 2 calls, got %d", calls.Load())
	}
	if len(results) != 1 || results[0].Title != "Result" {
		t.Fatalf("unexpected results: %#v", results)
	}
}

func TestClientDoesNotRetryBadRequest(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":{"message":"bad","type":"invalid_request_error","param":null,"code":"invalid_request"}}`))
	}))
	defer server.Close()

	client := New(server.URL, "key")
	client.MaxRetries = 3
	_, err := client.Search(context.Background(), "query", SearchOptions{})
	if err == nil {
		t.Fatal("expected an error")
	}
	apiError, ok := err.(*APIError)
	if !ok {
		t.Fatalf("expected APIError, got %T", err)
	}
	if apiError.StatusCode != http.StatusBadRequest || apiError.Code != "invalid_request" {
		t.Fatalf("unexpected error: %#v", apiError)
	}
	if calls.Load() != 1 {
		t.Fatalf("expected 1 call, got %d", calls.Load())
	}
}
