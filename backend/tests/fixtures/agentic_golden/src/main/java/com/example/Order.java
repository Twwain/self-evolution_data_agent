package com.example;

import javax.persistence.*;

@Entity
@Table(name = "orders")
public class Order {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne
    @JoinColumn(name = "customer_id", nullable = false)
    private Customer customer;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", length = 20)
    private OrderStatus status;

    @Column(name = "total_amount")
    private java.math.BigDecimal totalAmount;

    @Column(name = "created_at")
    private java.time.LocalDateTime createdAt;

    @Embedded
    private Address shippingAddress;
}
